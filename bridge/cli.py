"""The command line: three commands, and one way of reporting trouble.

`check` asks whether a peer harness could be used right now. `run` performs one
bounded turn against one. `record` writes one local message into the session
without calling anybody.

Three things about this file are worth knowing before reading it.

**Both `check` and `run` go through the same four-way switch.** All four
branches now lead to a real connector: Codex, Claude Code, ZCode and Hermes
Agent. An identifier that is not one of the four is an honest failure. There is
no fallback, no stub that answers as though a harness had, and no undocumented
way to reach a program anyway.

**The working directory is decided here, and nowhere else.** A peer runs in the
directory `--project` names, or, when there is none, in a neutral empty
directory made for the command and taken away again on every way out - including
a failure and being stopped. That is the only source of the fact. Nothing under
a message's `## Body` heading is read anywhere in Agent Bridge, so no text a peer
or a plan wrote can put a directory in front of a harness. A courier-only
connector - one whose harness cannot read a file without also being able to
write one - is never given a project: `--project` is refused for it here,
before anything is read or started, and its peer runs in the neutral directory
every time.

**Every failure looks the same.** One plain sentence saying what happened, then
one thing to do next, on the error stream, with a nonzero exit. That includes the
argument parser's own complaints, which are turned into the same shape rather
than being allowed to print in a different voice.

Being stopped is not a failure of that kind, so it reads differently: a
termination or hangup signal caught while a program was running says so in one
plain sentence and exits nonzero. There is nothing to do next except run it
again, and by the time that message is printed the cleanup has already happened.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
import tempfile
from typing import Iterator, Optional, Sequence

from . import connectors, record as record_module, runner
from .errors import BridgeError, Failure
from .peer import DEFAULT_TIMEOUT_SECONDS, Deadline, SignalStop

PROGRAM = "agent-bridge"

#: How a neutral working directory is named, so that one left behind by a
#: machine failure is recognisable for what it was.
NEUTRAL_PREFIX = "agent-bridge-neutral-"


class _Parser(argparse.ArgumentParser):
    """An argument parser whose complaints are ordinary Agent Bridge failures."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise BridgeError(Failure.USAGE_ERROR, detail=message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROGRAM,
        description="Connect two coding-agent harnesses through their "
        "official command-line programs.",
    )
    subcommands = parser.add_subparsers(dest="command")

    check = subcommands.add_parser(
        "check", help="report whether a peer harness could be used right now"
    )
    check.add_argument("--peer", required=True)

    run = subcommands.add_parser(
        "run", help="perform one bounded turn against a peer harness"
    )
    run.add_argument("--peer", required=True)
    run.add_argument("--session", required=True)
    run.add_argument("--project")
    run.add_argument("--review-base")
    run.add_argument("--review-head")
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)

    record = subcommands.add_parser(
        "record", help="write one local message into the session"
    )
    record.add_argument("--session", required=True)
    record.add_argument("--kind", required=True)
    record.add_argument("--local")
    record.add_argument("--peer")
    record.add_argument("--workflow")
    record.add_argument("--project")
    record.add_argument("--baseline")
    record.add_argument("--replace", action="store_true")

    return parser


def _read_body() -> str:
    """The substantive Markdown always arrives on standard input."""
    return sys.stdin.read()


def _remove_neutral(path: str, during: Optional[BaseException]) -> None:
    """Take the neutral working directory away, and be loud if it will not go.

    A directory left behind is the thing a person actually has to deal with, and
    nothing else would tell them it is there, so a removal that fails outranks
    whatever else went wrong and names both.
    """
    try:
        shutil.rmtree(path)
    except OSError as failed:
        if during is not None:
            raise BridgeError(
                Failure.CLEANUP_FAILURE,
                detail=(
                    "the neutral working directory {0} could not be removed "
                    "after the command failed ({1}): {2}".format(
                        path, during, failed
                    )
                ),
            )
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="the neutral working directory {0} could not be removed: "
            "{1}".format(path, failed),
        )


@contextlib.contextmanager
def _peer_directory(project: Optional[str]) -> Iterator[str]:
    """The directory a peer runs in, and the exact point a made-up one goes.

    `--project` names the one real directory a peer may read, and it becomes the
    directory the peer's program is started in. Without it the peer gets a
    neutral empty directory, made for this command and removed when it ends - on
    every way out, including a failure and being stopped - so a turn with no
    project has nothing to read and leaves nothing behind.

    A directory that was named but is not there is a mistake on the command
    line, and is reported as one before anything is started.
    """
    if project is not None:
        directory = os.path.abspath(project)
        if not os.path.isdir(directory):
            raise BridgeError(
                Failure.USAGE_ERROR,
                detail="--project is not a directory: {0}".format(directory),
            )
        yield directory
        return
    try:
        neutral = tempfile.mkdtemp(prefix=NEUTRAL_PREFIX)
    except OSError as exc:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="no neutral working directory could be made ({0}); name a "
            "readable one with --project instead".format(exc),
        )
    try:
        yield neutral
    except BaseException as exc:
        _remove_neutral(neutral, exc)
        raise
    _remove_neutral(neutral, None)


def _check(args: argparse.Namespace) -> str:
    # Readiness is the connector's own answer, and it costs nothing: finding the
    # program, its version, this computer, the harness's own sign-in question,
    # and its own listing of the switches this connector passes. No model turn,
    # and no real project - the questions are asked in a neutral directory made
    # for this command and taken away again afterwards. The whole deadline is
    # available to them, which is a ceiling rather than a wait: four short
    # programs do not use it, and one that hangs is a broken machine.
    connector = connectors.resolve(args.peer)
    with _peer_directory(None) as cwd:
        return connector.check(Deadline(DEFAULT_TIMEOUT_SECONDS), cwd)


def _run(args: argparse.Namespace) -> str:
    if (args.review_base is None) != (args.review_head is None):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--review-base and --review-head are required together",
        )
    connector = connectors.resolve(args.peer)
    # A connector says it is courier-only by carrying the name; the two that
    # can be pointed at a project simply do not carry it.
    if args.project is not None and getattr(connector, "COURIER_ONLY", False):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--project is not accepted for {0}, which is given no "
            "project and answers only on what it is sent".format(args.peer),
        )
    body = _read_body()
    if not body.strip():
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="there was no message to send on standard input",
        )
    with _peer_directory(args.project) as cwd:
        # The connector is what the runner is given as its command builder. The
        # runner calls it with the turn's deadline, so the connector's own
        # prechecks run inside that deadline, and takes back the fixed argument
        # vector to run. The working directory is bound here, where it was
        # decided, and the runner never sees a connector at all.
        def build(deadline: Deadline) -> connectors.PeerCommand:
            return connector.build_command(deadline, cwd)

        return runner.run_turn(
            session_dir=args.session,
            peer_id=args.peer,
            body=body,
            build_command=build,
            timeout_seconds=args.timeout,
            review_base=args.review_base,
            review_head=args.review_head,
        ).response_path


def _record(args: argparse.Namespace) -> str:
    return record_module.record(
        session_dir=args.session,
        kind=args.kind,
        body=_read_body(),
        local=args.local,
        peer=args.peer,
        workflow=args.workflow,
        project=args.project,
        baseline=args.baseline,
        replace=args.replace,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one command. Returns the process exit status."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        if not getattr(args, "command", None):
            raise BridgeError(
                Failure.USAGE_ERROR, detail="name a command: check, run, record"
            )
        if args.command == "check":
            written = _check(args)
        elif args.command == "run":
            written = _run(args)
        else:
            written = _record(args)
    except SignalStop as stopped:
        sys.stderr.write(str(stopped) + "\n")
        return 1
    except BridgeError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    sys.stdout.write(written + "\n")
    return 0

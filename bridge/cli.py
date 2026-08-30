"""The command line: three commands, and one way of reporting trouble.

`check` asks whether a peer harness could be used right now. `run` performs one
bounded turn against one. `record` writes one local message into the session
without calling anybody.

Two things about this file are worth knowing before reading it.

**Both `check` and `run` go through the same five-way switch**, and in this build
every one of the five branches reports that no connector ships yet. That is the
honest state of the work: the runner, the record and the safeguards are here, and
the five connectors are not. Nothing pretends otherwise, and there is no
fallback, no stub that answers as though a harness had, and no undocumented way
to reach a program anyway.

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
import sys
from typing import Optional, Sequence

from . import connectors, record as record_module
from .errors import BridgeError, Failure
from .peer import DEFAULT_TIMEOUT_SECONDS, SignalStop

PROGRAM = "agent-bridge"


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
    record.add_argument("--head")
    record.add_argument("--waived")
    record.add_argument("--replace", action="store_true")

    return parser


def _read_body() -> str:
    """The substantive Markdown always arrives on standard input."""
    return sys.stdin.read()


def _check(args: argparse.Namespace) -> str:
    # Resolving the identifier is the whole of `check` in this build: the switch
    # knows all five names and ships no connector for any of them. The raise
    # afterwards is not decoration - it guarantees this command can never report
    # readiness for a harness nothing here is able to call.
    connectors.resolve(args.peer)
    raise BridgeError(Failure.CONNECTOR_UNAVAILABLE, detail=args.peer)


def _run(args: argparse.Namespace) -> str:
    if (args.review_base is None) != (args.review_head is None):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--review-base and --review-head are required together",
        )
    # The connector resolved here is what the runner is given as its command
    # builder: the runner generates the review evidence first, then calls this
    # connector with that exact path and with the turn's deadline, so it can
    # name the file in the restriction switches of the fixed argument vector it
    # composes, declare the two paths it granted, and run any precheck of its
    # own inside the same deadline. The runner then runs that vector. This
    # build ships no connector, so the turn stops here - there is nothing to
    # hand over - rather than inventing a peer.
    connectors.resolve(args.peer)
    raise BridgeError(Failure.CONNECTOR_UNAVAILABLE, detail=args.peer)


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
        head=args.head,
        waived=args.waived,
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

"""The frozen Format 2 command line and uniform failure rendering.

`check` asks whether one fixed target is ready without spending a model turn.
`run` reads target and project only from its immutable session. `record` creates
that session or writes a neutral note. Substantive Markdown always arrives on
standard input.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import sys
import tempfile
from typing import Iterator, Optional, Sequence

from . import connectors, record as record_module, runner
from .errors import BridgeError, Failure
from .peer import DEFAULT_TIMEOUT_SECONDS, Deadline, SignalStop

PROGRAM = "agent-bridge"
NEUTRAL_PREFIX = "agent-bridge-neutral-"


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # type: ignore[override]
        raise BridgeError(Failure.USAGE_ERROR, detail=message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROGRAM,
        description="Send one bounded Markdown message to a supported target.",
    )
    subcommands = parser.add_subparsers(dest="command")

    check = subcommands.add_parser(
        "check", help="report whether a target can be used right now"
    )
    check.add_argument("--peer", required=True)

    run = subcommands.add_parser(
        "run", help="perform one bounded call for an existing session"
    )
    run.add_argument("--session", required=True)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)

    record = subcommands.add_parser(
        "record", help="create a session or add one neutral note"
    )
    record.add_argument("--session", required=True)
    record.add_argument("--kind", required=True)
    record.add_argument("--initiator")
    record.add_argument("--peer")
    record.add_argument("--project")

    return parser


def _read_body() -> str:
    return sys.stdin.read()


def _remove_neutral(path: str, during: Optional[BaseException]) -> None:
    try:
        shutil.rmtree(path)
    except OSError as failed:
        if during is not None:
            raise BridgeError(
                Failure.CLEANUP_FAILURE,
                detail="the neutral working directory {0} could not be removed "
                "after the command failed ({1}): {2}".format(
                    path, during, failed
                ),
            )
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="the neutral working directory {0} could not be removed: "
            "{1}".format(path, failed),
        )


@contextlib.contextmanager
def _check_directory() -> Iterator[str]:
    try:
        neutral = tempfile.mkdtemp(prefix=NEUTRAL_PREFIX)
    except OSError as exc:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="no neutral working directory could be made: {0}".format(exc),
        )
    try:
        yield neutral
    except BaseException as exc:
        _remove_neutral(neutral, exc)
        raise
    _remove_neutral(neutral, None)


def _check(args: argparse.Namespace) -> connectors.CheckResult:
    connector = connectors.resolve(args.peer)
    with _check_directory() as cwd:
        return connector.check(Deadline(DEFAULT_TIMEOUT_SECONDS), cwd)


def _run(args: argparse.Namespace) -> str:
    return runner.run_turn(
        session_dir=args.session,
        body=_read_body(),
        timeout_seconds=args.timeout,
        warning_writer=lambda warning: sys.stderr.write(
            "Warning: {0}\n".format(warning)
        ),
    ).response_path


def _record(args: argparse.Namespace) -> str:
    return record_module.record(
        session_dir=args.session,
        kind=args.kind,
        body=_read_body(),
        initiator=args.initiator,
        peer=args.peer,
        project=args.project,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        if not getattr(args, "command", None):
            raise BridgeError(
                Failure.USAGE_ERROR, detail="name a command: check, run, record"
            )
        if args.command == "check":
            checked = _check(args)
            sys.stdout.write(checked.message + "\n")
            for warning in checked.warnings:
                sys.stdout.write("Warning: {0}\n".format(warning))
            return 0
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

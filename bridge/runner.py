"""One bounded courier call derived entirely from an immutable session.

The runner validates the body and connector transport before publishing the
request. It then starts one fresh target process, publishes one final textual
answer, and exits. A failed target leaves the truthful request and no invented
response. There is no retry or implicit history.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import contextlib
import math
import os
import shutil
import tempfile
from typing import Callable, Iterator, NamedTuple, Optional, Tuple

from . import connectors, session as session_module
from .connectors import (
    COMMAND_LINE_BODY_LIMIT,
    PeerCommand,
    argument_space_limit,
    argument_space_used,
)
from .errors import BridgeError, Failure
from .locking import session_lock
from .peer import Deadline, run_bounded

NEUTRAL_PREFIX = "agent-bridge-neutral-"

# Tests may replace the production connector builder only by calling this
# function directly. No command-line, environment, file, or configuration path
# exposes this seam.
CommandBuilder = Callable[[Deadline, str], PeerCommand]
WarningWriter = Callable[[str], None]


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
def _target_directory(project: Optional[str]) -> Iterator[str]:
    """Yield the immutable project, or a task-owned neutral empty directory."""
    if project is not None:
        if not os.path.isdir(project):
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="the recorded project is not a directory: {0}".format(project),
            )
        yield project
        return
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


def apply_transport(
    command: PeerCommand, body: str
) -> Tuple[Tuple[str, ...], str]:
    """Bind the body to standard input or one qualified final argument."""
    if command.body_argument is None:
        if command.stdin_body_limit is not None:
            size = len(body.encode("utf-8"))
            if size > command.stdin_body_limit:
                raise BridgeError(
                    Failure.USAGE_ERROR,
                    detail="the message is {0} bytes and this peer silently "
                    "truncates standard input above {1} bytes; send a shorter "
                    "message".format(size, command.stdin_body_limit),
                )
        return command.argv, body
    if "\x00" in body:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="the message contains a NUL byte, which this peer's "
            "command-line transport cannot carry; remove it and send the "
            "message again",
        )
    size = len(body.encode("utf-8"))
    if size > COMMAND_LINE_BODY_LIMIT:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="the message is {0} bytes and this peer takes it on its "
            "command line, which Agent Bridge caps at {1} bytes; send a "
            "shorter message".format(size, COMMAND_LINE_BODY_LIMIT),
        )
    argv = command.argv + (command.body_argument + body,)
    used = argument_space_used(argv, command.env)
    limit = argument_space_limit()
    if used > limit:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="the message, the command and this environment together "
            "come to {0} bytes, more than the {1} the operating system allows "
            "for starting a program once headroom is kept; send a shorter "
            "message or start Agent Bridge with a smaller environment".format(
                used, limit
            ),
        )
    return argv, ""


class TurnResult(NamedTuple):
    request_sequence: int
    response_sequence: int
    response_path: str


def run_turn(
    session_dir: str,
    body: str,
    timeout_seconds: float,
    build_command: Optional[CommandBuilder] = None,
    warning_writer: Optional[WarningWriter] = None,
) -> TurnResult:
    """Publish one request and one response for the session's fixed target."""
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        raise BridgeError(Failure.USAGE_ERROR, detail="--timeout must be a number")
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise BridgeError(
            Failure.USAGE_ERROR, detail="--timeout must be greater than zero"
        )
    if not body or not body.strip():
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="there was no message to send on standard input",
        )

    deadline = Deadline(timeout)
    record = session_module.read_session(session_dir)
    if record.project is not None and connectors.is_courier_only(record.peer):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="the session records a project for {0}, which is courier-only; "
            "include the needed evidence in the body or choose a project-capable "
            "target".format(record.peer),
        )
    connector = connectors.resolve(record.peer)

    with session_lock(session_dir):
        with _target_directory(record.project) as cwd:
            deadline.check("composing the peer command")
            if build_command is None:
                command = connector.build_command(deadline, cwd)
            else:
                command = build_command(deadline, cwd)
            deadline.check("composing the peer command")
            if os.path.abspath(command.cwd) != os.path.abspath(cwd):
                raise BridgeError(
                    Failure.SESSION_INVALID,
                    detail="the connector did not use the session-derived directory",
                )
            argv, stdin_text = apply_transport(command, body)

            request_sequence = session_module.next_sequence(session_dir)
            if warning_writer is not None:
                for warning in command.warnings:
                    warning_writer(warning)
            session_module.publish(
                session_module.message_path(
                    session_dir,
                    request_sequence,
                    session_module.INITIATOR_TO_PEER_SUFFIX,
                ),
                session_module.initiator_to_peer_text(
                    request_sequence, record.initiator, record.peer, body
                ),
            )

            call = run_bounded(
                argv=argv,
                cwd=command.cwd,
                env=command.env,
                stdin_text=stdin_text,
                deadline=deadline,
            )
            if call.returncode != 0:
                raise BridgeError(
                    Failure.PEER_FAILURE,
                    detail="exit {0}: {1}".format(
                        call.returncode, call.stderr.strip()
                    ),
                )
            response = call.stdout
            if command.response_parser is not None:
                response = command.response_parser(response)
            if not response.strip():
                raise BridgeError(Failure.EMPTY_RESPONSE, detail=command.argv[0])

            response_sequence = session_module.next_sequence(session_dir)
            response_path = session_module.publish(
                session_module.message_path(
                    session_dir,
                    response_sequence,
                    session_module.PEER_TO_INITIATOR_SUFFIX,
                ),
                session_module.peer_to_initiator_text(
                    response_sequence, record.peer, record.initiator, response
                ),
            )

    return TurnResult(
        request_sequence=request_sequence,
        response_sequence=response_sequence,
        response_path=response_path,
    )

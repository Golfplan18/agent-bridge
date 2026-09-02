"""One bounded turn: send one message, get one answer, publish or fail.

This is the whole of what Agent Bridge does at run time. It publishes the
outgoing text as the next message, starts the peer once, waits for one answer
inside one deadline, and either publishes that answer or reports exactly what
went wrong. There is no retry, no queue, no second attempt with a longer wait,
and nothing that carries on after the function returns.

What gets written down when something goes wrong is the part worth being exact
about.

Where the call itself did not finish cleanly - the peer exited badly, said
nothing, or ran past the deadline - nothing at all is published. Whatever text
was captured may be a fragment of an answer the peer never finished writing, and
a fragment must not be mistaken for the peer's reply.

Where a request was published at all, it stays put in every failing case,
because it truthfully records what was sent. The failure itself is written down
by the workflow afterwards, with `record --kind technical-error`.

The peer command is built here rather than handed in ready-made. Composing the
fixed argument vector for a harness is a connector's work, but a connector has
inexpensive prechecks of its own to run first - a version, a sign-in, a
restriction probe - and those are programs. They belong inside this turn's
single deadline rather than before it started: there is no second way to start a
program and no separate budget for one. So this function takes a builder and
calls it with that deadline, and reads the deadline on both sides of the call,
because a builder can use the whole of it up.

Running that vector once, safely, inside the deadline is still this function's
work. Keeping those apart is why the runner never imports a connector.

How the body reaches the program is the connector's declaration and the
runner's act. Standard input is the transport wherever the program has one, and
the body is written there. For a program that has none, the connector says so
on the command it returns, and the runner appends the body to the vector as
exactly one final argument - never split, never through a shell - after first
refusing a body too large for a command line. Too large is measured the way
the operating system measures it: the whole argument vector, the whole
environment and the pointers that carry them, together, against the limit
this computer reports for starting a process, with half of that limit kept
back as headroom for what the kernel counts and this code cannot see. That
refusal, like every other failure before the call, comes before the request is
published, so nothing on the disk says a message was sent when none was.

`--review-base` and `--review-head` name the two commits a review request refers
to. When both are given they are copied onto the published answer, together with
the number of the request it answers, so that whoever reads the session
afterwards can see what the peer was asked about. That is all they are:
provenance, written down once and conditioning nothing.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
from typing import Callable, NamedTuple, Optional, Tuple

from . import session as session_module
from .connectors import PeerCommand
from .errors import BridgeError, Failure
from .locking import session_lock
from .peer import Deadline, run_bounded


class TurnResult(NamedTuple):
    """What one completed turn produced: the two messages it left behind."""

    request_sequence: int
    response_sequence: int
    response_path: str


#: What to assume the operating system allows for starting a process, in
#: bytes, when it will not say. The POSIX floor is far lower, but no computer
#: this project has been qualified on reports anything under a megabyte; the
#: figure is only a stand-in, and half of it is kept back as headroom too.
FALLBACK_ARG_MAX = 262144

#: What one pointer costs in the block the kernel builds: eight bytes for each
#: argument and each environment entry, plus one terminating null for each of
#: the two lists.
POINTER_BYTES = 8


def _arg_max() -> int:
    """The operating system's own figure for a new process's argument block."""
    try:
        reported = os.sysconf("SC_ARG_MAX")
    except (AttributeError, OSError, ValueError):
        return FALLBACK_ARG_MAX
    if not isinstance(reported, int) or reported <= 0:
        return FALLBACK_ARG_MAX
    return reported


def command_line_capacity() -> int:
    """How much of the process-creation payload this computer may be given.

    The kernel's figure covers the argument strings, the environment strings,
    the pointers to both, and things this code cannot see: the program's path,
    alignment, auxiliary entries, and on some systems a cap on any single
    argument. Half of the figure is kept back for those, explicitly, so what
    remains is what the vector, the environment and the body may add up to.
    """
    limit = _arg_max()
    return limit - limit // 2


def _payload_bytes(
    argv: Tuple[str, ...], env: Tuple[Tuple[str, str], ...]
) -> int:
    """The encoded size of the block the kernel will be asked to build."""
    total = 2 * POINTER_BYTES
    for argument in argv:
        total += len(argument.encode("utf-8")) + 1 + POINTER_BYTES
    for name, value in env:
        total += (
            len(name.encode("utf-8"))
            + 1
            + len(value.encode("utf-8"))
            + 1
            + POINTER_BYTES
        )
    return total


def _handover(
    command: PeerCommand, body: str
) -> Tuple[Tuple[str, ...], str]:
    """The vector to run and the text to write to standard input.

    For a program that reads standard input the vector is the connector's own
    and the whole body goes down the pipe. For one that takes the body on its
    command line the body becomes one final argument, prefixed as the
    connector said, and standard input gets nothing.

    Two things a command-line argument cannot carry are refused here rather
    than discovered when the process is created: a body holding a NUL byte,
    which no argument list can hold at all, and a body that would make the
    whole block - vector, environment and pointers together - larger than this
    computer allows less its headroom. Neither is ever cut down to fit.
    """
    if command.body_argument is None:
        return command.argv, body
    program = os.path.basename(command.argv[0])
    if "\x00" in body:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail=(
                "the message contains a NUL byte, which {0} takes on its "
                "command line and no command-line argument can carry".format(
                    program
                )
            ),
        )
    argv = command.argv + (command.body_argument + body,)
    payload = _payload_bytes(argv, command.env)
    capacity = command_line_capacity()
    if payload > capacity:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail=(
                "the message is {0} bytes and {1} takes it on its command "
                "line, where it, the rest of the command and the environment "
                "come to {2} bytes against the {3} this computer allows once "
                "headroom is kept; send a shorter message, or use a peer that "
                "reads standard input".format(
                    len(body.encode("utf-8")), program, payload, capacity
                )
            ),
        )
    return argv, ""


def run_turn(
    session_dir: str,
    peer_id: str,
    body: str,
    build_command: Callable[[Deadline], PeerCommand],
    timeout_seconds: float,
    review_base: Optional[str] = None,
    review_head: Optional[str] = None,
) -> TurnResult:
    """Perform one bounded turn against a peer harness.

    `build_command` is the connector. It is called once, inside the deadline,
    with this turn's deadline, and returns the fixed argument vector to run.
    Anything it starts to answer that call must go through the shared bounded
    process runner with that same deadline.

    Supplying `review_base` and `review_head` makes this a review request. The
    only difference that makes is to the answer, which then carries the number
    of the request it answers and the two commit names the request referred to.
    Nothing about how the turn runs changes, and nothing is conditioned on them.

    One turn happens in this order, holding the session lock: compose the
    command, with the deadline read either side; publish the request; run the
    peer; and publish the answer. Every failure raises. A failure before the
    request is published leaves no request behind, because nothing was sent, and
    no failure here publishes an answer.
    """
    deadline = Deadline(timeout_seconds)
    review = review_base is not None and review_head is not None

    record = session_module.read_session(session_dir)
    if peer_id != record.peer:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="this session's peer is {0}, not {1}".format(
                record.peer, peer_id
            ),
        )

    with session_lock(session_dir):
        # The connector composes the argument vector now, and runs its own
        # prechecks while it is about it. That is why the deadline is read on
        # both sides of the call: a builder that used the whole of it up must
        # not then be allowed to start a peer.
        deadline.check("composing the peer command")
        command = build_command(deadline)
        deadline.check("composing the peer command")
        argv, stdin_text = _handover(command, body)

        # The request is published once there is a command to run and the peer
        # is about to be started. Until then nothing would have been sent, and a
        # request message on the disk would be saying otherwise.
        request_sequence = session_module.next_sequence(session_dir)
        session_module.publish(
            session_module.message_path(
                session_dir,
                request_sequence,
                session_module.LOCAL_TO_PEER_SUFFIX,
            ),
            session_module.local_to_peer_text(
                request_sequence, record.local, record.peer, body
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
        if not response.strip():
            raise BridgeError(Failure.EMPTY_RESPONSE, detail=command.argv[0])

        response_sequence = session_module.next_sequence(session_dir)
        response_path = session_module.publish(
            session_module.message_path(
                session_dir,
                response_sequence,
                session_module.PEER_TO_LOCAL_SUFFIX,
            ),
            session_module.peer_to_local_text(
                response_sequence,
                record.peer,
                record.local,
                response,
                review_request=request_sequence if review else None,
                review_base=review_base if review else None,
                review_head=review_head if review else None,
            ),
        )

    return TurnResult(
        request_sequence=request_sequence,
        response_sequence=response_sequence,
        response_path=response_path,
    )

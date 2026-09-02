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
the body is written there. For a program that has none, the connector sets
`body_argument` on the command it composes, and this module binds the body to
that prefix as exactly one final argument - never split, never through a shell
- and sends nothing on standard input. Three refusals stand in front of that,
all before a request is published or a program started: a body larger than the
fixed limit; a body containing a NUL byte, which no argument can carry; and an
argument list that together with the inherited environment would not fit what
the operating system allows a new process, measured the way the kernel measures
it and with explicit headroom kept back for what it counts and this code cannot
see. Nothing is truncated, split or written to a file to get round any of them;
the person is told to send a shorter message. Because every refusal comes first,
nothing on the disk ever says a message was sent when none was.

`--review-base` and `--review-head` name the two commits a review request refers
to. When both are given they are copied onto the published answer, together with
the number of the request it answers, so that whoever reads the session
afterwards can see what the peer was asked about. That is all they are:
provenance, written down once and conditioning nothing.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Optional, Tuple

from . import session as session_module
from .connectors import (
    COMMAND_LINE_BODY_LIMIT,
    PeerCommand,
    argument_space_limit,
    argument_space_used,
)
from .errors import BridgeError, Failure
from .locking import session_lock
from .peer import Deadline, run_bounded


def apply_transport(
    command: PeerCommand, body: str
) -> Tuple[Tuple[str, ...], str]:
    """Where the body goes: the argument vector to run and the standard input.

    For a command without `body_argument` this changes nothing: the vector is
    the connector's and the body is the standard input. For one with it, the
    body is bound to the prefix as one final argument and standard input is
    left empty - after the three refusals below, each of which names what was
    wrong and what to do, and each of which happens before anything is started
    or published.
    """
    if command.body_argument is None:
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
    """What one completed turn produced: the two messages it left behind."""

    request_sequence: int
    response_sequence: int
    response_path: str


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
        argv, stdin_text = apply_transport(command, body)

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

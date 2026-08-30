"""One bounded turn: send one message, get one answer, publish or fail.

This is the whole of what Agent Bridge does at run time. It publishes the
outgoing text as the next message, starts the peer once, waits for one answer
inside one deadline, and either publishes that answer or reports exactly what
went wrong. There is no retry, no queue, no second attempt with a longer wait,
and nothing that carries on after the function returns.

The rule that matters most is what gets written down when something goes wrong.

A **failed or ambiguous call publishes no response at all.** A peer that exited
badly, said nothing, ran past the deadline, ended its review with something that
is not one of the three exact verdict lines, or reviewed a repository that
changed underneath it, leaves no answer in the record. Nothing that later reads
the session can mistake a failure for a reply. The request message that was
already published stays, because it truthfully records what was sent; the
failure itself is written down by the workflow with
`record --kind technical-error`.

`REJECT` and `ASK_USER` are not failures. They are real decisions, made by a
reviewer that did its job, and they are published like any other answer - they
simply do not unlock anything.

The peer command is built here rather than handed in ready-made. Composing the
fixed argument vector for a harness is a connector's work, but it cannot be
done before the turn starts: a connector confines a reviewing peer by naming
the exact review-evidence file in the restrictions it applies, and that path
does not exist until the diff has been written. So this function takes a
builder, generates the evidence, and then calls the builder with that exact
path - `None` when the turn is not a review. Two things follow. The connector
can name the one file the peer may read outside the project, and the
connector's own inexpensive prechecks happen inside this turn's single deadline
rather than before it started.

Running that vector once, safely, inside the deadline is still this function's
work. Keeping those apart is why the runner never imports a connector.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Optional

from . import gitgate, session as session_module
from .connectors import PeerCommand
from .errors import BridgeError, Failure
from .locking import session_lock
from .peer import Deadline, run_bounded
from .verdict import ACCEPT, read_verdict


class TurnResult(NamedTuple):
    """What one completed turn produced.

    `git_unlocked` is the only thing in Agent Bridge that says the remaining Git
    work may go ahead, and it is true in exactly one situation, spelled out in
    `run_turn` below.
    """

    request_sequence: int
    response_sequence: int
    response_path: str
    verdict: Optional[str]
    git_unlocked: bool


def run_turn(
    session_dir: str,
    peer_id: str,
    body: str,
    build_command: Callable[[Optional[str]], PeerCommand],
    timeout_seconds: float,
    project: Optional[str] = None,
    review_base: Optional[str] = None,
    review_head: Optional[str] = None,
) -> TurnResult:
    """Perform one bounded turn against a peer harness.

    `build_command` is the connector. It is called once, inside the deadline and
    after any review evidence has been written, with the exact path of that
    evidence file - or `None` when this turn is not a review - and returns the
    fixed argument vector to run.

    Supplying `review_base` and `review_head` makes this an external review and
    switches on the commit safeguards: the repository, baseline, head and clean
    worktree are all checked before the peer is asked anything, and the
    repository, cleanliness and head are checked again after it has answered.

    Every failure raises, leaving the Git finish line locked and no response
    message behind. That includes a failure to build the command: the evidence
    is deleted just the same, and nothing is published.
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
        sealed = None
        base = None
        head = None
        if review:
            if not project:
                raise BridgeError(
                    Failure.USAGE_ERROR,
                    detail="a review needs --project",
                )
            sealed = session_module.read_sealed_implementation(session_dir)
            if sealed is None:
                raise BridgeError(
                    Failure.NO_IMPLEMENTATION_BASELINE, detail=session_dir
                )
            base, head = gitgate.before_review_checks(
                project, sealed, review_base, review_head, deadline
            )

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

        evidence_path = None
        try:
            if review:
                evidence_path = gitgate.generate_review_evidence(
                    project, base, head, deadline
                )
            # The connector composes the argument vector now, knowing where the
            # evidence is, so it can name that exact file among the paths the
            # peer is allowed to read.
            command = build_command(evidence_path)
            call = run_bounded(
                argv=command.argv,
                cwd=command.cwd,
                env=command.env,
                stdin_text=body,
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
                raise BridgeError(
                    Failure.EMPTY_RESPONSE, detail=command.argv[0]
                )
            verdict = None
            if review:
                result = read_verdict(response)
                if not result.ok:
                    raise BridgeError(result.failure, detail=command.argv[0])
                verdict = result.verdict
        finally:
            gitgate.delete_review_evidence(evidence_path)

        if review:
            gitgate.after_review_checks(project, sealed, head, deadline)

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
                review_base=base,
                review_head=head,
            ),
        )

    return TurnResult(
        request_sequence=request_sequence,
        response_sequence=response_sequence,
        response_path=response_path,
        verdict=verdict,
        # The one place the Git finish line opens: the call succeeded, the
        # verdict is exactly ACCEPT, the answer is bound to this request and to
        # the sealed baseline and head, and both rounds of repository checks
        # passed - the second of which confirmed the worktree is still on the
        # head that was reviewed.
        git_unlocked=bool(review and verdict == ACCEPT),
    )

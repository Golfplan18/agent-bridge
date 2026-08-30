"""One bounded turn: send one message, get one answer, publish or fail.

This is the whole of what Agent Bridge does at run time. It publishes the
outgoing text as the next message, starts the peer once, waits for one answer
inside one deadline, and either publishes that answer or reports exactly what
went wrong. There is no retry, no queue, no second attempt with a longer wait,
and nothing that carries on after the function returns.

The rule that matters most is what gets written down when something goes wrong.

**No failed call ever publishes an authoritative verdict.** That is the rule,
and everything below is what it means in the two situations it covers.

Where the call itself did not finish cleanly - the peer exited badly, said
nothing, or ran past the deadline - nothing at all is published. Whatever text
was captured may be a fragment of an answer the peer never finished writing, and
a fragment must not be mistaken for the peer's reply. The same holds when the
repository changed underneath a review: the answer describes a state that no
longer exists.

Where the peer finished cleanly, exit zero with real output, and only its final
line is wrong, the prose is kept. It is published as an **ordinary message**,
the same shape any non-review answer takes, and it carries none of the
`Review-*` fields that bind an answer to a request and to two commits. So it
holds no external-review authority, unlocks nothing, and cannot be mistaken for
a decision. The call still fails with `INVALID_VERDICT`, Git stays locked, and
nothing is rewritten, re-read for an intention, or retried. Acceptance requires
a fresh review call. The point is narrow: a reviewer that did good work and
fumbled one line should not have that work thrown away.

In every failing case the request message that was already published stays,
because it truthfully records what was sent; the failure itself is written down
by the workflow with `record --kind technical-error`.

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

    Every failure raises and leaves the Git finish line locked. Almost every
    failure also leaves no response message behind - including a failure to
    build the command, where the evidence is deleted just the same and nothing
    is published. The single exception is `INVALID_VERDICT`: a peer that exited
    cleanly with real output whose final line is not a verdict has its text kept
    as an ordinary message, published with no `Review-*` fields, which is why it
    can hold no authority. The call fails all the same.
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
            unusable_from = None
            if review:
                result = read_verdict(response)
                if result.failure is Failure.INVALID_VERDICT:
                    # The peer finished cleanly and wrote real prose; only its
                    # last line is wrong. Keep the prose - published below with
                    # every binding field withheld - and still fail.
                    unusable_from = command.argv[0]
                elif not result.ok:
                    raise BridgeError(result.failure, detail=command.argv[0])
                else:
                    verdict = result.verdict
        finally:
            gitgate.delete_review_evidence(evidence_path)

        # An answer that carries no verdict is bound to nothing, so the second
        # round of repository checks has nothing left to protect. Running them
        # anyway could only replace INVALID_VERDICT with a different and less
        # accurate failure.
        bound = review and unusable_from is None
        if bound:
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
                review_request=request_sequence if bound else None,
                review_base=base if bound else None,
                review_head=head if bound else None,
            ),
        )

        if unusable_from is not None:
            # Published, but as an ordinary message with no Review-* fields: it
            # holds no authority, unlocks nothing, and the call still fails.
            raise BridgeError(
                Failure.INVALID_VERDICT,
                detail="{0}; the response was kept as {1}".format(
                    unusable_from, response_path
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

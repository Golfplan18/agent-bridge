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

Where a request was published at all, it stays put in every failing case,
because it truthfully records what was sent; the failure itself is written down
by the workflow with `record --kind technical-error`.

`REJECT` and `ASK_USER` are not failures. They are real decisions, made by a
reviewer that did its job, and they are published like any other answer - they
simply do not unlock anything.

The peer command is built here rather than handed in ready-made. Composing the
fixed argument vector for a harness is a connector's work, but it cannot be
done before the turn starts: a connector tells a reviewing peer where the
evidence is by naming the exact review-evidence file in the restrictions it
applies, and that path does not exist until the diff has been written. So this
function takes a builder, generates the evidence, and then calls the builder
with that exact path - `None` when the turn is not a review - and with this
turn's deadline. Two things follow. The connector can name the exact evidence
file, which sits outside the project, and every inexpensive precheck it runs -
a version, a sign-in, a restriction probe - happens inside this turn's single
deadline rather than before it started. Those prechecks go through the same
bounded process runner with the same deadline; there is no second way to start
a program and no budget of its own for one.

Running that vector once, safely, inside the deadline is still this function's
work. Keeping those apart is why the runner never imports a connector.

**A reviewer has to be shown to have read the evidence, not trusted to have.**
Three things together do that, and the third is the one that makes it a rule
rather than a hope.

The connector states what it granted the peer read access to - the project root
and the evidence file - and both statements have to match, exactly, what this
turn made. The exact bytes written are hashed and checked again the moment the
peer finishes, so a file that was replaced, truncated or removed voids the turn
rather than producing a verdict about something nobody can identify.

And the evidence file ends with one line nobody could have written but this
turn: a fresh unpredictable token. The instruction this function appends to the
outgoing body names the beginning of that line and never its value, so the only
way to produce the value is to open the file and read it. **No answer becomes an
acceptance unless that value comes back.** A review whose response does not
contain it is treated exactly as a review whose last line is not a verdict: the
peer's prose is kept as an ordinary message that binds to nothing and carries no
authority, the call fails with `REVIEW_EVIDENCE_NOT_DELIVERED`, Git stays
locked, and acceptance requires a fresh review call. Good findings are not
thrown away for a missing token, and a missing token is not forgiven either.

The value appearing anywhere in the response is enough. What is being proved is
that the file reached the peer and could be read, not that a model weighed every
line of it - no check could show the second thing, and pretending otherwise
would be worse than saying which one this is.

The published request names the evidence file in a `Review-Evidence:` line, and
it is worth being exact about what that line is for. It is this turn's own note
of what it generated, kept so that whoever reads the session afterwards can see
what the reviewer was pointed at. It is not what makes delivery possible and
never reaches the peer: a header sits above the body, and the peer receives only
the body. What tells a peer where to read are the connector's restriction
switches, and what shows it read is the token above.

All of that is also why nothing is published until the evidence exists, the
command has been composed and the declared paths agree. Until all three are true
nothing has been sent, and a request message on disk would be saying otherwise.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
from typing import Callable, NamedTuple, Optional

from . import gitgate, session as session_module
from .connectors import PeerCommand
from .errors import BridgeError, Failure
from .locking import session_lock
from .peer import Deadline, run_bounded, stopped_by_signal
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


def _canonical(path: Optional[str]) -> Optional[str]:
    """One spelling of a path, so two names for one file compare equal.

    Symbolic links are followed, so a link and its target are seen as the one
    file they are. Two limits are worth saying out loud rather than leaving to
    be discovered.

    Letter case is not folded. On a filesystem that ignores case - the ordinary
    macOS one does - two spellings that differ only in case name the same file,
    and this comparison calls them different, so the turn is refused. That is
    the safe way round, and a connector that hands back the path it was given
    never meets it.

    And a link is looked at once, here. A link changed after this moment points
    somewhere else and nothing looks again. Under the same-user trust boundary
    that is not a defence this can offer: whoever could move the link could
    equally read the file.
    """
    if not path:
        return None
    return os.path.realpath(path)


def _require_declared_access(
    command: PeerCommand,
    project: Optional[str],
    evidence: Optional["gitgate.ReviewEvidence"],
) -> None:
    """Refuse to start a peer unless the connector named these exact paths.

    A connector says what it granted the peer read access to. Here that claim
    is held against what this turn actually made, with both spellings resolved
    through any symbolic links first so that two names for one file are seen as
    one file. A review must name a real evidence file, an ordinary turn must
    name none, and a turn with no project must be granted no project.

    Disagreement is not a small tidiness problem. It means the peer would be
    reading something other than the difference this turn wrote, so nothing is
    sent and no request is published.
    """
    if _canonical(command.project_root) != _canonical(project):
        raise BridgeError(
            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
            detail="the connector granted the project root {0}, not {1}".format(
                command.project_root, project
            ),
        )
    wanted = evidence.path if evidence is not None else None
    if _canonical(command.review_evidence) != _canonical(wanted):
        raise BridgeError(
            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
            detail="the connector granted the evidence file {0}, not {1}".format(
                command.review_evidence, wanted
            ),
        )


def run_turn(
    session_dir: str,
    peer_id: str,
    body: str,
    build_command: Callable[[Optional[str], Deadline], PeerCommand],
    timeout_seconds: float,
    project: Optional[str] = None,
    review_base: Optional[str] = None,
    review_head: Optional[str] = None,
) -> TurnResult:
    """Perform one bounded turn against a peer harness.

    `build_command` is the connector. It is called once, inside the deadline and
    after any review evidence has been written, with the exact path of that
    evidence file - or `None` when this turn is not a review - and this turn's
    deadline, and returns the fixed argument vector to run. Anything it starts
    to answer that call must go through the shared bounded process runner with
    that same deadline.

    Supplying `review_base` and `review_head` makes this an external review and
    switches on the commit safeguards: the repository, baseline, head and clean
    worktree are all checked before the peer is asked anything, and the
    repository, cleanliness and head are checked again after it has answered -
    whatever the answer looked like.

    One review turn happens in this order, holding the session lock: check the
    repository; write the evidence, which ends with this turn's own token, and
    record what it holds; compose the command, with the deadline checked either
    side; require the connector's declared paths to match; publish the request,
    naming the evidence and carrying the instruction to copy the token back; run
    the peer; confirm the evidence is still the bytes that were written; read
    the verdict and require the token; delete the evidence, which happens on the
    way out of any of those steps whatever their outcome; check the repository
    again; and only then publish the answer.

    Every failure raises and leaves the Git finish line locked. A failure before
    the request is published leaves no request behind, because nothing was sent.
    Almost every failure also leaves no response message - including a failure
    to build the command, where the evidence is deleted just the same and
    nothing is published. There are two exceptions, and they are the same
    exception twice: a peer that exited cleanly with real output whose final
    line is not a verdict (`INVALID_VERDICT`), and one whose answer never
    mentions the evidence token (`REVIEW_EVIDENCE_NOT_DELIVERED`). Both have
    their text kept as an ordinary message, published with no `Review-*` fields,
    which is why neither can hold any authority. The call fails all the same.
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

        evidence = None
        # The evidence file exists for the whole of this block, and for
        # stretches of it no program is running - composing the command,
        # publishing the request, reading the answer. A termination or a hangup
        # in one of those has to raise rather than end the process where it
        # stands, or the `finally` below never gets to delete the file.
        with stopped_by_signal():
            try:
                if review:
                    evidence = gitgate.generate_review_evidence(
                        project, base, head, deadline
                    )
                # The connector composes the argument vector now, knowing where
                # the evidence is, so it can name that exact file among the
                # paths the peer is allowed to read. Its own prechecks happen in
                # here, which is why the deadline is read on both sides of the
                # call.
                deadline.check("composing the peer command")
                command = build_command(
                    evidence.path if evidence is not None else None, deadline
                )
                deadline.check("composing the peer command")
                _require_declared_access(command, project, evidence)

                # A review carries one more thing than the workflow wrote: this
                # turn's own instruction to copy the evidence token back. It is
                # appended to the outgoing text, so what is sent and what the
                # record shows are the same words.
                outgoing = body
                if evidence is not None:
                    outgoing = (
                        session_module.body_block(body)
                        + gitgate.REVIEW_EVIDENCE_INSTRUCTION
                    )

                # Only now has anything been sent, so only now is there a
                # request worth recording - and it names the file the reviewer
                # was given.
                request_sequence = session_module.next_sequence(session_dir)
                session_module.publish(
                    session_module.message_path(
                        session_dir,
                        request_sequence,
                        session_module.LOCAL_TO_PEER_SUFFIX,
                    ),
                    session_module.local_to_peer_text(
                        request_sequence,
                        record.local,
                        record.peer,
                        outgoing,
                        review_evidence=(
                            evidence.path if evidence is not None else None
                        ),
                    ),
                )

                call = run_bounded(
                    argv=command.argv,
                    cwd=command.cwd,
                    env=command.env,
                    stdin_text=outgoing,
                    deadline=deadline,
                )
                if evidence is not None:
                    # Before anything is read out of the answer: the reviewer
                    # must have had the exact file this turn wrote, or there is
                    # no saying what the answer is about.
                    gitgate.verify_review_evidence(evidence)
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
                unusable = None
                if review:
                    result = read_verdict(response)
                    if result.failure is Failure.INVALID_VERDICT:
                        # The peer finished cleanly and wrote real prose; only
                        # its last line is wrong. Keep the prose - published
                        # below with every binding field withheld - and still
                        # fail.
                        unusable = (
                            Failure.INVALID_VERDICT,
                            command.argv[0],
                        )
                    elif not result.ok:
                        raise BridgeError(
                            result.failure, detail=command.argv[0]
                        )
                    elif evidence is None or evidence.token not in response:
                        # A well-formed verdict from a peer that never quoted
                        # the token cannot have been reached by reading the
                        # difference. It is treated exactly as a fumbled last
                        # line: the prose is kept, bound to nothing, and the
                        # call fails.
                        unusable = (
                            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
                            "{0} answered without the token that was only in "
                            "the evidence file".format(command.argv[0]),
                        )
                    else:
                        verdict = result.verdict
            finally:
                gitgate.delete_review_evidence(evidence)

        # These run whatever the answer looked like. A repository that moved
        # while the review was being written is the more important fact, and
        # keeping a malformed reviewer's prose is only worth doing once the
        # code it describes is known to be still there.
        if review:
            gitgate.after_review_checks(project, sealed, head, deadline)

        bound = review and unusable is None

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

        if unusable is not None:
            # Published, but as an ordinary message with no Review-* fields: it
            # holds no authority, unlocks nothing, and the call still fails.
            failure, why = unusable
            raise BridgeError(
                failure,
                detail="{0}; the response was kept as {1}".format(
                    why, response_path
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

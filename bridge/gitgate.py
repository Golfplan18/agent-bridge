"""The facts about the repository that a review has to be true of.

A verdict is only worth something if it describes the exact code that was
looked at. This module establishes what "the exact code" means, and refuses to
let a review float free of it.

Four facts matter, and each is read from Git rather than believed from a
message: which repository this is, which commit the work started from, which
commit is being reviewed, and whether anything is uncommitted. They are checked
once before the peer is called and again after it has finished, because the most
ordinary way for a review to become untrue is for somebody to commit while it
was being written.

Identity is deliberately two things at once: the real path of the repository's
top level after following any symbolic links, and the set of commits its history
begins from. A path alone could be a different repository moved into place; root
commits alone could be a clone somewhere else. Together they say "the same
repository, in the same place".

Every Git command here is given the same fixed set of overrides, so that as
little as possible of what a repository says about itself can decide what a
reviewer sees or start a program while this module reads.

Replacement objects are switched off, because Git lets a repository say
"wherever you see this commit, read that one instead" and a review that honoured
such a mapping would show a reviewer contents that are not the commit named by
`Review-Head`. External difference programs and text-conversion filters are
switched off for the same reason. A filesystem-monitor helper, a hook directory
and automatic housekeeping are switched off because a supposedly read-only check
that runs somebody else's program has already had an effect before the peer was
even started.

**One way in is left open, and it is left open knowingly.** A repository can
name a content filter - `filter=<name>` in its own `.gitattributes`, and
`filter.<name>.clean` in its local configuration - and Git runs that program
while working out whether the worktree is clean. Agent Bridge does not switch
that off, because there is no fixed switch that does: filters are named one at a
time and cannot be turned off as a class, and the one mechanism that would
suppress them needs a Git new enough to take `--attr-source`, a per-repository
lookup to name an empty tree, and would change what the evidence file itself
shows. So it is stated rather than pretended about. The exposure needs the local
`.git/config` of the repository under review to define such a filter, which
under the same-user trust boundary is the user's own configuration; a person who
does not want it should not point a review at a repository whose local
configuration they did not write.

Cleanliness is asked for in full. Untracked files are requested explicitly
rather than left to the repository's own preference, because a repository may
ask Git to stop mentioning them; ignored entries are asked for as well. Anything
at all means the worktree holds something no commit contains.

The reviewer gets no shell and no Git. What it gets instead is one file: the
cumulative difference between the two commits, generated here, once, and then
one more line - a fresh unpredictable token, written by this turn and reachable
no other way. That token is what turns "the peer was told where the evidence is"
into "the peer read it": the runner asks for it back and refuses to open the Git
finish line without it. The file lives outside both the project and the session
record, is derived evidence rather than a second source of truth, and is deleted
on every way out. The exact bytes written are hashed at the same moment, so the
file the peer had can be shown afterwards to be the file this turn wrote.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from typing import Iterable, NamedTuple, Optional, Sequence, Tuple

from . import peer as peer_module
from .errors import BridgeError, Failure
from .session import SealedImplementation

#: Every review-evidence file carries this prefix, so a check can assert that
#: none of them outlived the turn that made one.
REVIEW_EVIDENCE_PREFIX = "agent-bridge-review-evidence-"
REVIEW_EVIDENCE_SUFFIX = ".diff"

#: The last line of every review-evidence file begins with this, and ends with a
#: value generated fresh for that one turn. Spacing is exact, because the
#: instruction sent to the peer quotes this prefix and nothing else.
EVIDENCE_TOKEN_PREFIX = "Agent-Bridge-Evidence-Token: "

#: How many random bytes the token is made of. Thirty-two hexadecimal characters
#: cannot be guessed, worked out from the outgoing message, or repeated by
#: accident between two turns.
EVIDENCE_TOKEN_BYTES = 16

#: What the runner appends to the outgoing body of a review request. It names
#: the prefix and never the value: the value exists only inside the evidence
#: file, so quoting it back is the one thing a peer that did not open the file
#: cannot do.
REVIEW_EVIDENCE_INSTRUCTION = """
---

## Proof that you received and read the review evidence

The last line of the review-evidence file this call granted you begins with
`{0}`. Open that file, read it, and copy that line into
your response.

Agent Bridge refuses any review whose response does not contain that value,
whatever verdict the response carries, and no such review can unlock the Git
finish line.
""".format(
    EVIDENCE_TOKEN_PREFIX.strip()
)


class RepositoryIdentity(NamedTuple):
    """Which repository this is: where it really lives, and where it began."""

    path: str
    root_commits: Tuple[str, ...]


class ReviewEvidence(NamedTuple):
    """The review evidence a peer was given, and proof of what it held.

    `digest` is the SHA-256 of the exact bytes written into `path`. It is taken
    at the moment of writing so that, once the peer has finished, the same bytes
    can be shown to be there still.

    `token` is the unpredictable value on the file's last line, made fresh for
    this one turn. The digest proves the file did not change under the peer;
    the token proves the peer opened it. Neither alone is enough: a file nobody
    read is unchanged too, and a value quoted back says nothing about what the
    file held at the end.
    """

    path: str
    digest: str
    token: str


def _git_env() -> Tuple[Tuple[str, str], ...]:
    """The environment Git runs in.

    Three inherited variables are removed because they would silently override
    the repository named on the command line. Git is told never to prompt for
    anything, and never to take an optional lock, so reading the repository
    cannot change it.
    """
    env = dict(os.environ)
    for inherited in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(inherited, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return tuple(sorted(env.items()))


#: The settings every gate command overrides, and why each one is here. This is
#: the complete list, not a summary of one: a content filter named through the
#: repository's own `.gitattributes` is not on it and still runs, for the
#: reasons given at the top of this file.
#:
#: - `core.fsmonitor` names a filesystem-monitor helper that Git starts while
#:   reading the worktree. A repository could point it at any program at all, so
#:   `git status` would have run somebody else's code before the peer existed.
#: - `core.hooksPath` names where Git looks for hooks. None of the commands
#:   below is meant to run one; pointing the directory at nothing means none can
#:   be found however Git changes.
#: - `gc.auto` and `maintenance.auto` let Git decide to start housekeeping of
#:   its own. Reading a repository must not begin work in it.
#: - `diff.external` and `core.attributesFile` are how a repository asks for its
#:   own difference program or text-conversion filter to produce what a reviewer
#:   reads. What a reviewer reads is Git's own output for two named commits.
_NO_REPOSITORY_PROGRAMS: Tuple[str, ...] = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=" + os.devnull,
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
    "-c",
    "diff.external=",
    "-c",
    "core.attributesFile=" + os.devnull,
)


def _git(
    project: str,
    args: Sequence[str],
    deadline: "peer_module.Deadline",
    allowed_status: Iterable[int] = (),
    failure: Failure = Failure.REPOSITORY_UNREADABLE,
) -> "peer_module.CompletedCall":
    """One Git command, as a fixed argument vector, inside the deadline.

    Runs in the task-owned process group like everything else this turn starts,
    so a Git process that hangs is terminated the same way a peer is. A status
    the caller did not name as an expected answer is a failure - by default
    `REPOSITORY_UNREADABLE`, carrying Git's own words.

    Two kinds of override are applied to every one of these commands. Replacement
    objects are switched off, so a mapping stored in the repository cannot make a
    commit read as a different commit; without it, the contents a reviewer judged
    could differ from the commit written into `Review-Head`. And the settings
    listed above, which would otherwise let the repository choose a program for
    Git to run, are switched off. That list is exact and does not cover content
    filters, which the top of this file explains.
    """
    argv = ["git", "-C", project, "--no-pager", "--no-replace-objects"]
    argv.extend(_NO_REPOSITORY_PROGRAMS)
    argv.extend(args)
    result = peer_module.run_bounded(
        argv=argv,
        cwd=project if os.path.isdir(project) else os.getcwd(),
        env=_git_env(),
        stdin_text="",
        deadline=deadline,
        spawn_failure=failure,
    )
    if result.returncode != 0 and result.returncode not in tuple(allowed_status):
        raise BridgeError(
            failure,
            detail=result.stderr.strip() or "git exited {0}".format(
                result.returncode
            ),
        )
    return result


def toplevel(project: str, deadline: "peer_module.Deadline") -> str:
    """The real path of the repository's top level, symbolic links resolved."""
    if not os.path.isdir(project):
        raise BridgeError(Failure.REPOSITORY_UNREADABLE, detail=project)
    result = _git(project, ["rev-parse", "--show-toplevel"], deadline)
    return os.path.realpath(result.stdout.strip())


def resolve_commit(
    project: str, revision: str, deadline: "peer_module.Deadline"
) -> str:
    """The full commit id a revision names, or `REPOSITORY_UNREADABLE`."""
    result = _git(
        project, ["rev-parse", "--verify", revision + "^{commit}"], deadline
    )
    return result.stdout.strip()


def root_commits(
    project: str,
    commit: str,
    deadline: "peer_module.Deadline",
    failure: Failure = Failure.REPOSITORY_UNREADABLE,
) -> Tuple[str, ...]:
    """The commits this history begins from, sorted so comparison is stable."""
    result = _git(
        project,
        ["rev-list", "--max-parents=0", commit],
        deadline,
        failure=failure,
    )
    return tuple(sorted(result.stdout.split()))


def resolve_identity(
    project: str, baseline: str, deadline: "peer_module.Deadline"
) -> RepositoryIdentity:
    """Work out which repository this is, for sealing or for comparing."""
    return RepositoryIdentity(
        path=toplevel(project, deadline),
        root_commits=root_commits(project, baseline, deadline),
    )


def is_ancestor(
    project: str, base: str, head: str, deadline: "peer_module.Deadline"
) -> bool:
    """Does `base` come before `head` on the same history?

    Git answers this with its exit status: nought means yes, one means no.
    Anything else is Git failing to answer, not an answer.
    """
    result = _git(
        project,
        ["merge-base", "--is-ancestor", base, head],
        deadline,
        allowed_status=(1,),
    )
    return result.returncode == 0


def require_clean(project: str, deadline: "peer_module.Deadline") -> None:
    """Refuse to go on unless no commit is missing anything in the worktree.

    Git is asked for every untracked file and for ignored entries as well as
    changed ones, and any entry at all means the worktree is not clean. An
    untracked file counts because a reviewer reading the project would see it
    and no commit would contain it; an ignored file counts for exactly the same
    reason, and being ignored by Git says nothing about whether a reviewing peer
    can read it.

    Untracked files are asked for by name on the command line rather than left
    to the repository's own preference. A repository may set
    `status.showUntrackedFiles` to `no`, and then an ordinary untracked file -
    and every ignored one with it - simply does not appear in the answer. A
    worktree that hides what it contains would otherwise pass as clean, and a
    reviewer would be reading files no commit holds.

    Nothing is ever deleted here. The failure names what was found so that a
    person can decide what to do with their own files.
    """
    result = _git(
        project,
        ["status", "--porcelain", "--untracked-files=all", "--ignored"],
        deadline,
    )
    entries = [line.strip() for line in result.stdout.splitlines()]
    entries = [line for line in entries if line]
    if not entries:
        return
    found = entries[0]
    if len(entries) > 1:
        found = "{0} and {1} more".format(found, len(entries) - 1)
    raise BridgeError(
        Failure.DIRTY_WORKTREE, detail="{0}: {1}".format(project, found)
    )


def current_head(project: str, deadline: "peer_module.Deadline") -> str:
    """The commit the worktree is on right now."""
    return _git(project, ["rev-parse", "HEAD"], deadline).stdout.strip()


def check_identity(
    project: str,
    sealed: SealedImplementation,
    deadline: "peer_module.Deadline",
) -> None:
    """Confirm this is the repository the session sealed, or say so."""
    here = toplevel(project, deadline)
    if here != sealed.repository_path:
        raise BridgeError(
            Failure.REPOSITORY_CHANGED,
            detail="{0} is not the sealed {1}".format(
                here, sealed.repository_path
            ),
        )
    roots = root_commits(
        project, sealed.baseline, deadline, failure=Failure.REPOSITORY_CHANGED
    )
    if roots != tuple(sealed.root_commits):
        raise BridgeError(
            Failure.REPOSITORY_CHANGED,
            detail="history now begins at {0}".format(" ".join(roots)),
        )


def before_review_checks(
    project: str,
    sealed: SealedImplementation,
    review_base: str,
    review_head: str,
    deadline: "peer_module.Deadline",
) -> Tuple[str, str]:
    """Everything that must be true before a reviewer is asked anything.

    In order: the repository is the sealed one; the baseline is the sealed
    baseline; the baseline comes before a genuinely different head; there is
    nothing uncommitted; and the worktree is on the head being reviewed. The
    order matters, because the first thing that is wrong is the thing worth
    telling somebody about.

    Returns the two full commit ids the review is bound to.
    """
    check_identity(project, sealed, deadline)
    base = resolve_commit(project, review_base, deadline)
    if base != sealed.baseline:
        raise BridgeError(
            Failure.BASELINE_CHANGED,
            detail="{0} is not the sealed baseline {1}".format(
                base, sealed.baseline
            ),
        )
    head = resolve_commit(project, review_head, deadline)
    if base == head:
        raise BridgeError(
            Failure.BASELINE_NOT_ANCESTOR,
            detail="baseline and head are both {0}".format(head),
        )
    if not is_ancestor(project, base, head, deadline):
        raise BridgeError(
            Failure.BASELINE_NOT_ANCESTOR,
            detail="{0} does not come before {1}".format(base, head),
        )
    require_clean(project, deadline)
    here = current_head(project, deadline)
    if here != head:
        raise BridgeError(
            Failure.HEAD_CHANGED,
            detail="the worktree is on {0}, not {1}".format(here, head),
        )
    return base, head


def after_review_checks(
    project: str,
    sealed: SealedImplementation,
    reviewed_head: str,
    deadline: "peer_module.Deadline",
) -> None:
    """The same repository, still clean, still on the head that was reviewed."""
    check_identity(project, sealed, deadline)
    require_clean(project, deadline)
    here = current_head(project, deadline)
    if here != reviewed_head:
        raise BridgeError(
            Failure.HEAD_CHANGED,
            detail="the worktree moved to {0} during the review".format(here),
        )


def generate_review_evidence(
    project: str,
    base: str,
    head: str,
    deadline: "peer_module.Deadline",
) -> ReviewEvidence:
    """Write the cumulative difference between two commits to one owned file.

    External difference programs and text-conversion filters are switched off
    four ways, on the command line and in the configuration this one call uses,
    and replacement objects are off for every Git call this module makes, so
    what a reviewer reads is what Git itself produced for the two commits named
    - not the output of something the repository asked to be run, and not some
    other commit standing in for one of them.

    The file is made in the system temporary area - outside the project and
    outside the session record - because it is derived evidence, not part of the
    permanent account of the work.

    One line is added after the difference: a token made from fresh random bytes
    for this turn alone. A real difference contains nothing a peer could not
    have worked out from the change itself, so there would otherwise be nothing
    in the file that proves it was opened. The token is written here and named
    nowhere else, so a peer can only produce it by reading the file, and the
    runner requires it back before any acceptance counts.

    Bytes are written, and those same bytes are hashed, rather than text being
    handed to an encoder twice: the digest has to describe the file on disk
    exactly, or checking it afterwards would prove nothing.

    A temporary area that is full, unwritable or missing is an ordinary thing to
    run into, so it is reported as `REVIEW_EVIDENCE_UNAVAILABLE` with something
    to do about it. A write that fails part way through takes its half-written
    file with it - and when that removal fails too, a partly written file is
    left on the disk, which is a cleanup failure and is reported as one. Saying
    only that the evidence was unavailable would leave somebody with a file
    nobody told them about.
    """
    argv = [
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "{0}..{1}".format(base, head),
    ]
    result = _git(project, argv, deadline)
    token = secrets.token_hex(EVIDENCE_TOKEN_BYTES)
    payload = (
        result.stdout + "\n" + EVIDENCE_TOKEN_PREFIX + token + "\n"
    ).encode("utf-8")
    try:
        handle, path = tempfile.mkstemp(
            prefix=REVIEW_EVIDENCE_PREFIX, suffix=REVIEW_EVIDENCE_SUFFIX
        )
    except OSError as exc:
        raise BridgeError(Failure.REVIEW_EVIDENCE_UNAVAILABLE, detail=str(exc))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException as exc:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as removal:
            raise BridgeError(
                Failure.CLEANUP_FAILURE,
                detail=(
                    "the partly written review evidence {0} could not be "
                    "removed after it failed to be written ({1}): {2}".format(
                        path, exc, removal
                    )
                ),
            )
        raise BridgeError(
            Failure.REVIEW_EVIDENCE_UNAVAILABLE,
            detail="{0}: {1}".format(path, exc),
        )
    return ReviewEvidence(
        path=path, digest=hashlib.sha256(payload).hexdigest(), token=token
    )


def verify_review_evidence(evidence: ReviewEvidence) -> None:
    """Confirm the peer still had the exact file this turn wrote for it.

    Read immediately after the peer has finished and before the file is
    removed. A file that is gone, shorter, or different in any byte means the
    reviewer was not judging the difference this turn generated, so the turn is
    void - there is no way to tell what it was actually looking at.

    What this does not catch, stated plainly rather than implied away: it
    compares two moments, not the whole time in between. Evidence replaced with
    bytes identical to the ones written passes, which is the intended answer -
    the file still holds the difference this turn generated. Evidence that was
    something else for a while and was put back before the peer finished passes
    too, and that one is a genuine gap: this check would not see it.
    """
    try:
        with open(evidence.path, "rb") as stream:
            found = hashlib.sha256(stream.read()).hexdigest()
    except OSError as exc:
        raise BridgeError(
            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
            detail="{0}: {1}".format(evidence.path, exc),
        )
    if found != evidence.digest:
        raise BridgeError(
            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
            detail="{0} is no longer the difference this turn wrote".format(
                evidence.path
            ),
        )


def delete_review_evidence(evidence: Optional[ReviewEvidence]) -> None:
    """Remove the evidence file. Failing to is a visible failure, not a shrug."""
    if evidence is None:
        return
    try:
        os.unlink(evidence.path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="review evidence {0}: {1}".format(evidence.path, exc),
        )

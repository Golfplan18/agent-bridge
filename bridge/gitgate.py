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

The reviewer gets no shell and no Git. What it gets instead is one file: the
cumulative difference between the two commits, generated here, once, with
external difference programs and text-conversion filters switched off so that
nothing in the repository's own configuration can decide what a reviewer sees.
That file lives outside both the project and the session record, is derived
evidence rather than a second source of truth, and is deleted on every way out.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
import tempfile
from typing import Iterable, NamedTuple, Optional, Sequence, Tuple

from . import peer as peer_module
from .errors import BridgeError, Failure
from .session import SealedImplementation

#: Every review-evidence file carries this prefix, so a check can assert that
#: none of them outlived the turn that made one.
REVIEW_EVIDENCE_PREFIX = "agent-bridge-review-evidence-"
REVIEW_EVIDENCE_SUFFIX = ".diff"


class RepositoryIdentity(NamedTuple):
    """Which repository this is: where it really lives, and where it began."""

    path: str
    root_commits: Tuple[str, ...]


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
    """
    argv = ["git", "-C", project, "--no-pager"]
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


def is_clean(project: str, deadline: "peer_module.Deadline") -> bool:
    """Is there nothing uncommitted at all, untracked files included?

    Any output whatsoever means no. An untracked file counts, because a
    reviewer reading the project would see it and no commit would contain it.
    """
    result = _git(project, ["status", "--porcelain"], deadline)
    return result.stdout.strip() == ""


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
    if not is_clean(project, deadline):
        raise BridgeError(Failure.DIRTY_WORKTREE, detail=project)
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
    if not is_clean(project, deadline):
        raise BridgeError(Failure.DIRTY_WORKTREE, detail=project)
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
) -> str:
    """Write the cumulative difference between two commits to one owned file.

    External difference programs and text-conversion filters are switched off
    four ways, on the command line and in the configuration this one call uses,
    so what a reviewer reads is what Git itself produced and not the output of
    something the repository asked to be run.

    The file is made in the system temporary area - outside the project and
    outside the session record - because it is derived evidence, not part of the
    permanent account of the work.
    """
    argv = [
        "-c",
        "diff.external=",
        "-c",
        "core.attributesFile=" + os.devnull,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "{0}..{1}".format(base, head),
    ]
    result = _git(project, argv, deadline)
    handle, path = tempfile.mkstemp(
        prefix=REVIEW_EVIDENCE_PREFIX, suffix=REVIEW_EVIDENCE_SUFFIX
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(result.stdout)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException as exc:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="review evidence {0}: {1}".format(path, exc),
        )
    return path


def delete_review_evidence(path: Optional[str]) -> None:
    """Remove the evidence file. Failing to is a visible failure, not a shrug."""
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="review evidence {0}: {1}".format(path, exc),
        )

"""Writing one local message into the session, without calling anybody.

A native package should never write a session file itself. It hands the runner
some text, says what kind of record it is, and the runner does the numbering,
the locking, the envelope and the atomic write. That way there is one writer for
the canonical record no matter which harness is driving, and one numbering
scheme that cannot disagree with itself.

There are six kinds and there will not be a seventh without editing this file.
Each one is written out in a plain switch below, because the complete list of
things that may be written into a session ought to be readable in one sitting.

What this command cannot do is the point of it existing:

- it never starts a peer harness;
- it never writes a `Review-Request`, `Review-Base` or `Review-Head` line;
- it never produces a verdict, and no body text can become one.

A user's waiver is recorded here, and a waiver is a different authority from an
acceptance: it is reported as `USER WAIVED`, it binds to one exact commit, and a
new commit invalidates it. It cannot paper over a changed repository, a moved
head or an unclean worktree - those are checked here, before anything is
written, and must be put right first.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
from typing import List, Optional

from . import gitgate, session as session_module
from .connectors import HARNESS_IDS
from .errors import BridgeError, Failure
from .locking import session_lock
from .peer import DEFAULT_TIMEOUT_SECONDS, Deadline

#: The six kinds, in the order the interface lists them. Nothing else is a kind.
RECORD_KINDS = (
    "session-create",
    "user-correction",
    "plan-approval",
    "technical-error",
    "implementation-start",
    "user-waiver",
)

#: What a waiver may be recorded against.
WAIVABLE = ("REJECT", "ERROR")


def _require(value: Optional[str], what: str) -> str:
    if not value:
        raise BridgeError(
            Failure.USAGE_ERROR, detail="{0} is required".format(what)
        )
    return value


def _require_harness(value: Optional[str], what: str) -> str:
    identifier = _require(value, what)
    if identifier not in HARNESS_IDS:
        raise BridgeError(Failure.UNKNOWN_HARNESS, detail=identifier)
    return identifier


def _publish_local_record(
    session_dir: str,
    kind: str,
    local: str,
    body: str,
    extra_headers: Optional[List[str]] = None,
) -> str:
    """Allocate the next number and write one local record under it."""
    sequence = session_module.next_sequence(session_dir)
    return session_module.publish(
        session_module.message_path(
            session_dir, sequence, session_module.LOCAL_RECORD_SUFFIX
        ),
        session_module.local_record_text(
            sequence, kind, local, body, extra_headers=extra_headers
        ),
    )


def _create_session(
    session_dir: str,
    body: str,
    local: Optional[str],
    peer: Optional[str],
    workflow: Optional[str],
    project: Optional[str],
) -> str:
    """Write `SESSION.md` once, and make the folder it lives in."""
    local_id = _require_harness(local, "--local")
    peer_id = _require_harness(peer, "--peer")
    chosen = _require(workflow, "--workflow")
    if chosen not in session_module.WORKFLOWS:
        raise BridgeError(Failure.USAGE_ERROR, detail="--workflow " + chosen)
    project_path = os.path.abspath(project) if project else None
    try:
        os.makedirs(session_module.messages_dir(session_dir), exist_ok=True)
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))
    with session_lock(session_dir):
        if os.path.exists(session_module.session_file(session_dir)):
            raise BridgeError(Failure.SESSION_EXISTS, detail=session_dir)
        return session_module.publish(
            session_module.session_file(session_dir),
            session_module.session_text(
                local_id, peer_id, chosen, body, project=project_path
            ),
        )


def _seal_implementation(
    session_dir: str,
    record: "session_module.SessionRecord",
    body: str,
    project: Optional[str],
    baseline: Optional[str],
    deadline: Deadline,
) -> str:
    """Seal the repository and baseline every later review is bound to."""
    project_path = os.path.abspath(_require(project, "--project"))
    revision = _require(baseline, "--baseline")
    if session_module.read_sealed_implementation(session_dir) is not None:
        raise BridgeError(
            Failure.IMPLEMENTATION_ALREADY_SEALED, detail=session_dir
        )
    commit = gitgate.resolve_commit(project_path, revision, deadline)
    identity = gitgate.resolve_identity(project_path, commit, deadline)
    return _publish_local_record(
        session_dir,
        "implementation-start",
        record.local,
        body,
        extra_headers=[
            "Repository-Path: {0}".format(identity.path),
            "Repository-Root-Commits: {0}".format(
                " ".join(identity.root_commits)
            ),
            "Baseline: {0}".format(commit),
        ],
    )


def _approve_plan(
    session_dir: str,
    record: "session_module.SessionRecord",
    body: str,
    replace: bool,
) -> str:
    """Write the numbered record first, then seal `PLAN.md` with the same text.

    The numbered record goes first so the approved text is preserved in the
    ordered account of the session even when it later replaces an earlier plan.
    The earlier plan stays readable in its own numbered message.
    """
    plan_path = session_module.plan_file(session_dir)
    exists = os.path.exists(plan_path)
    if exists and not replace:
        raise BridgeError(Failure.PLAN_SEALED, detail=plan_path)
    _publish_local_record(
        session_dir,
        "plan-approval",
        record.local,
        body,
        extra_headers=["Plan: {0}".format("REPLACED" if exists else "SEALED")],
    )
    return session_module.publish(plan_path, session_module.body_block(body))


def _waive(
    session_dir: str,
    record: "session_module.SessionRecord",
    body: str,
    project: Optional[str],
    head: Optional[str],
    waived: Optional[str],
    deadline: Deadline,
) -> str:
    """Record the user's waiver, bound to one exact commit that still stands."""
    project_path = os.path.abspath(_require(project, "--project"))
    revision = _require(head, "--head")
    verdict = _require(waived, "--waived")
    if verdict not in WAIVABLE:
        raise BridgeError(Failure.USAGE_ERROR, detail="--waived " + verdict)
    sealed = session_module.read_sealed_implementation(session_dir)
    if sealed is None:
        raise BridgeError(
            Failure.NO_IMPLEMENTATION_BASELINE, detail=session_dir
        )
    gitgate.check_identity(project_path, sealed, deadline)
    commit = gitgate.resolve_commit(project_path, revision, deadline)
    gitgate.require_clean(project_path, deadline)
    here = gitgate.current_head(project_path, deadline)
    if here != commit:
        raise BridgeError(
            Failure.HEAD_CHANGED,
            detail="the worktree is on {0}, not {1}".format(here, commit),
        )
    return _publish_local_record(
        session_dir,
        "user-waiver",
        record.local,
        body,
        extra_headers=[
            "Decision: USER WAIVED",
            "Waived-Head: {0}".format(commit),
            "Waived-Verdict: {0}".format(verdict),
        ],
    )


def record(
    session_dir: str,
    kind: str,
    body: str,
    local: Optional[str] = None,
    peer: Optional[str] = None,
    workflow: Optional[str] = None,
    project: Optional[str] = None,
    baseline: Optional[str] = None,
    head: Optional[str] = None,
    waived: Optional[str] = None,
    replace: bool = False,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Write one local record of one of the six kinds. Returns the path written.

    The substantive Markdown always arrives as `body`, read by the caller from
    standard input; empty or whitespace-only text is a usage error, because a
    record with nothing in it records nothing.
    """
    if kind not in RECORD_KINDS:
        raise BridgeError(Failure.UNKNOWN_RECORD_KIND, detail=kind)
    if not body or not body.strip():
        raise BridgeError(
            Failure.USAGE_ERROR, detail="the record body was empty"
        )

    if kind == "session-create":
        return _create_session(session_dir, body, local, peer, workflow, project)

    deadline = Deadline(timeout_seconds)
    session_record = session_module.read_session(session_dir)
    with session_lock(session_dir):
        if kind == "user-correction":
            return _publish_local_record(
                session_dir, kind, session_record.local, body
            )
        if kind == "technical-error":
            return _publish_local_record(
                session_dir, kind, session_record.local, body
            )
        if kind == "plan-approval":
            return _approve_plan(session_dir, session_record, body, replace)
        if kind == "implementation-start":
            return _seal_implementation(
                session_dir, session_record, body, project, baseline, deadline
            )
        if kind == "user-waiver":
            return _waive(
                session_dir,
                session_record,
                body,
                project,
                head,
                waived,
                deadline,
            )
    raise BridgeError(Failure.UNKNOWN_RECORD_KIND, detail=kind)

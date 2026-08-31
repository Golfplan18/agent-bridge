"""Writing one local message into the session, without calling anybody.

A native package should never write a session file itself. It hands the runner
some text, says what kind of record it is, and the runner does the numbering,
the locking, the envelope and the atomic write. That way there is one writer for
the canonical record no matter which harness is driving, and one numbering
scheme that cannot disagree with itself.

There are five kinds and there will not be a sixth without editing this file.
Each one is written out in a plain switch below, because the complete list of
things that may be written into a session ought to be readable in one sitting.

What this command cannot do is the point of it existing:

- it never starts a peer harness;
- it never writes a `Review-Request`, `Review-Base` or `Review-Head` line.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
from typing import List, Optional

from . import session as session_module
from .connectors import HARNESS_IDS
from .errors import BridgeError, Failure
from .locking import session_lock

#: The five kinds, in the order the interface lists them. Nothing else is a
#: kind.
RECORD_KINDS = (
    "session-create",
    "user-correction",
    "plan-approval",
    "technical-error",
    "implementation-start",
)


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


def _record_implementation_start(
    session_dir: str,
    record: "session_module.SessionRecord",
    body: str,
    project: Optional[str],
    baseline: Optional[str],
) -> str:
    """Write down where the work started, and condition nothing on it.

    The repository and the baseline the task began from are worth having in the
    ordered account of the session: a later reader can see which project was
    being worked on and what the work is measured from. That is the whole of it.
    Nothing here consults Git, nothing checks that the baseline names a commit
    that exists, and no later command is conditioned, withheld or bound by what
    is written. The baseline is recorded exactly as it was given.
    """
    project_path = os.path.abspath(_require(project, "--project"))
    revision = _require(baseline, "--baseline")
    return _publish_local_record(
        session_dir,
        "implementation-start",
        record.local,
        body,
        extra_headers=[
            "Repository-Path: {0}".format(project_path),
            "Baseline: {0}".format(revision),
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


def record(
    session_dir: str,
    kind: str,
    body: str,
    local: Optional[str] = None,
    peer: Optional[str] = None,
    workflow: Optional[str] = None,
    project: Optional[str] = None,
    baseline: Optional[str] = None,
    replace: bool = False,
) -> str:
    """Write one local record of one of the five kinds. Returns the path written.

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
            return _record_implementation_start(
                session_dir, session_record, body, project, baseline
            )
    raise BridgeError(Failure.UNKNOWN_RECORD_KIND, detail=kind)

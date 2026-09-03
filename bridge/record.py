"""Create one courier session or add one neutral note without calling a target.

This is the only local writer besides the runner. Both record kinds use the
same validation, session lock, sequence allocation, envelopes, and atomic
publication as a target call. The Markdown body remains inert application text.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
from typing import Optional

from . import session as session_module
from .connectors import HARNESS_IDS
from .errors import BridgeError, Failure
from .locking import session_lock

RECORD_KINDS = ("session-create", "note")


def _require(value: Optional[str], what: str) -> str:
    if not value:
        raise BridgeError(
            Failure.USAGE_ERROR, detail="{0} is required".format(what)
        )
    return value


def _require_peer(value: Optional[str]) -> str:
    identifier = _require(value, "--peer")
    if identifier not in HARNESS_IDS:
        raise BridgeError(Failure.UNKNOWN_HARNESS, detail=identifier)
    return identifier


def _project_path(project: Optional[str]) -> Optional[str]:
    if project is None:
        return None
    if not os.path.isabs(project):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--project must be an absolute existing directory",
        )
    if not os.path.isdir(project):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--project is not an existing directory: {0}".format(project),
        )
    if "\n" in project or "\r" in project or project != project.strip():
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--project cannot contain line breaks or surrounding whitespace",
        )
    return project


def _create_session(
    session_dir: str,
    body: str,
    initiator: Optional[str],
    peer: Optional[str],
    project: Optional[str],
) -> str:
    """Write the immutable Format 2 session record and allocate no number."""
    initiator_label = session_module.validate_initiator(
        _require(initiator, "--initiator")
    )
    peer_id = _require_peer(peer)
    project_directory = _project_path(project)
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
                initiator_label,
                peer_id,
                body,
                project=project_directory,
            ),
        )


def _publish_note(
    session_dir: str, record: "session_module.SessionRecord", body: str
) -> str:
    sequence = session_module.next_sequence(session_dir)
    return session_module.publish(
        session_module.message_path(
            session_dir, sequence, session_module.INITIATOR_RECORD_SUFFIX
        ),
        session_module.initiator_record_text(
            sequence, "note", record.initiator, body
        ),
    )


def record(
    session_dir: str,
    kind: str,
    body: str,
    initiator: Optional[str] = None,
    peer: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    """Create a session or add a note, returning the canonical path."""
    if kind not in RECORD_KINDS:
        raise BridgeError(Failure.UNKNOWN_RECORD_KIND, detail=kind)
    if not body or not body.strip():
        raise BridgeError(Failure.USAGE_ERROR, detail="the record body was empty")

    if kind == "session-create":
        return _create_session(session_dir, body, initiator, peer, project)

    if initiator is not None or peer is not None or project is not None:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="note accepts no --initiator, --peer, or --project argument",
        )
    session_record = session_module.read_session(session_dir)
    with session_lock(session_dir):
        return _publish_note(session_dir, session_record, body)

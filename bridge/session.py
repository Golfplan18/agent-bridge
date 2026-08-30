"""The session folder: how it is written, and how it is read back.

A session is a directory of Markdown files, and that is the whole of what Agent
Bridge remembers. There is no index, no cache, no status field and no side file
that has to agree with the messages. Everything a later turn needs is worked out
by looking at what is actually on disk - which is why a fresh task can pick up
work it never saw being done.

Three ideas hold this module together.

**Publication either happens or it does not.** A message is written to a
temporary file in the same directory, forced out to the disk, and then renamed
into its canonical name. A rename within a directory is atomic, so a reader sees
either no file or a complete one. A half-written message never appears under a
name anything would trust.

**Numbering is derived, not remembered.** The next sequence number is whatever is
one higher than the highest number already on disk. Nothing counts on behalf of
the directory, so nothing can disagree with it.

**The body is inert, structurally.** Header lines are read only from the block
between the title line and the first blank line. The text under `## Body` is
never looked at by any parser here. That is not a promise about intent; it is
where the code stops reading. Prose that looks like a header stays prose.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from . import BRIDGE_FORMAT
from .errors import BridgeError, Failure

SESSION_FILENAME = "SESSION.md"
MESSAGES_DIRNAME = "messages"
PLAN_FILENAME = "PLAN.md"

#: Every temporary file this module creates while publishing carries this
#: prefix, so a check can assert that none of them survived a failure.
TEMP_PREFIX = ".agent-bridge-publish-"

#: The three canonical message filename shapes.
LOCAL_TO_PEER_SUFFIX = "-local-to-peer.md"
PEER_TO_LOCAL_SUFFIX = "-peer-to-local.md"
LOCAL_RECORD_SUFFIX = "-local-record.md"

#: The workflows a session may declare.
WORKFLOWS: Tuple[str, ...] = ("planning", "programming-loop", "external-review")

BODY_HEADING = "## Body"

_SEQUENCE_PATTERN = re.compile(r"^(\d{4,})-")


class SessionRecord(NamedTuple):
    """What `SESSION.md` said, read fresh from disk.

    Written once when the session is created and never changed afterwards, so
    nothing in here can go stale: the two harnesses taking part, which workflow
    they are running, and the project when there is one.
    """

    directory: str
    bridge_format: str
    local: str
    peer: str
    workflow: str
    project: Optional[str]


class SealedImplementation(NamedTuple):
    """The repository and baseline this session sealed when work began.

    Every later external review is bound to these values. They come from the one
    `implementation-start` local record, read out of its header block - never
    from a peer's body, and never from a command line.
    """

    sequence: int
    repository_path: str
    root_commits: Tuple[str, ...]
    baseline: str


# -- paths ------------------------------------------------------------------


def session_file(session_dir: str) -> str:
    return os.path.join(session_dir, SESSION_FILENAME)


def messages_dir(session_dir: str) -> str:
    return os.path.join(session_dir, MESSAGES_DIRNAME)


def plan_file(session_dir: str) -> str:
    return os.path.join(session_dir, PLAN_FILENAME)


def format_sequence(sequence: int) -> str:
    """Sequence numbers are padded to at least four digits, and never fewer."""
    return "{0:04d}".format(sequence)


def message_path(session_dir: str, sequence: int, suffix: str) -> str:
    return os.path.join(
        messages_dir(session_dir), format_sequence(sequence) + suffix
    )


# -- atomic publication -----------------------------------------------------


def _fsync_directory(directory: str) -> None:
    """Force the directory entry itself out, so the rename survives a crash."""
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def publish(path: str, text: str) -> str:
    """Write one canonical file so that it is either whole or absent.

    The text goes into a task-owned temporary file beside the destination,
    is flushed and forced to disk, and only then renamed onto the canonical
    name. Any failure removes the temporary file and reports
    `PUBLICATION_FAILURE`, leaving the session exactly as it was.
    """
    directory = os.path.dirname(os.path.abspath(path))
    try:
        handle, temp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=directory)
    except OSError as exc:
        raise BridgeError(Failure.PUBLICATION_FAILURE, detail=str(exc))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        _fsync_directory(directory)
    except BaseException as exc:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except OSError:
            pass
        raise BridgeError(
            Failure.PUBLICATION_FAILURE, detail="{0}: {1}".format(path, exc)
        )
    return path


# -- envelopes --------------------------------------------------------------


def body_block(body: str) -> str:
    """The body exactly as supplied, ending in a newline so the file ends."""
    if body.endswith("\n"):
        return body
    return body + "\n"


def _compose(title: str, header_lines: Sequence[str], body: str) -> str:
    """Title, then the header block, then the body under its own heading."""
    header = "".join(line + "\n" for line in header_lines)
    return "{0}\n{1}\n{2}\n\n{3}".format(
        title, header, BODY_HEADING, body_block(body)
    )


def session_text(
    local: str,
    peer: str,
    workflow: str,
    body: str,
    project: Optional[str] = None,
) -> str:
    """`SESSION.md`, written once and never edited."""
    header_lines = [
        "Bridge-Format: {0}".format(BRIDGE_FORMAT),
        "Local: {0}".format(local),
        "Peer: {0}".format(peer),
        "Workflow: {0}".format(workflow),
    ]
    if project:
        header_lines.append("Project: {0}".format(project))
    return "# Session\n" + _compose("", header_lines, body)


def local_to_peer_text(sequence: int, local: str, peer: str, body: str) -> str:
    """A request going out to the peer. It never carries a `Review-` field."""
    return _compose(
        "# Message {0}".format(format_sequence(sequence)),
        ["From: {0}".format(local), "To: {0}".format(peer)],
        body,
    )


def peer_to_local_text(
    sequence: int,
    peer: str,
    local: str,
    body: str,
    review_request: Optional[int] = None,
    review_base: Optional[str] = None,
    review_head: Optional[str] = None,
) -> str:
    """A peer's answer, copied through unchanged.

    In review mode three more header lines are added, and every one of them is a
    fact the runner already held before it made the call: which request this
    answers, and the two commits the review is bound to. The peer supplies none
    of them.
    """
    header_lines = ["From: {0}".format(peer), "To: {0}".format(local)]
    if review_request is not None:
        header_lines.append(
            "Review-Request: {0}".format(format_sequence(review_request))
        )
        header_lines.append("Review-Base: {0}".format(review_base))
        header_lines.append("Review-Head: {0}".format(review_head))
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


def local_record_text(
    sequence: int,
    kind: str,
    local: str,
    body: str,
    extra_headers: Optional[Sequence[str]] = None,
) -> str:
    """A local record: no recipient, and never a `Review-` line of any kind."""
    header_lines = ["Record: {0}".format(kind), "From: {0}".format(local)]
    if extra_headers:
        header_lines.extend(extra_headers)
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


# -- cold reading -----------------------------------------------------------


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _header_block(text: str, title_prefix: str) -> Dict[str, str]:
    """Read the header block, and refuse to read one line further.

    The block starts after the title line and ends at the first blank line or
    the first line that begins a new heading. Everything below - including
    everything under `## Body` - is out of reach of this function, which is how
    body inertness is enforced rather than merely intended.
    """
    lines = _normalise(text).split("\n")
    if not lines or not lines[0].startswith(title_prefix):
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="expected a line starting {0!r}".format(title_prefix),
        )
    index = 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    fields = {}  # type: Dict[str, str]
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.startswith("#"):
            break
        name, separator, value = line.partition(":")
        if not separator:
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="header line is not 'Name: value': {0!r}".format(line),
            )
        fields[name.strip()] = value.strip()
        index += 1
    return fields


def _read_text(path: str, missing: Failure) -> str:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except FileNotFoundError:
        raise BridgeError(missing, detail=path)
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))


def read_session(session_dir: str) -> SessionRecord:
    """Read `SESSION.md` back from disk, every time it is needed."""
    if not os.path.isdir(session_dir):
        raise BridgeError(Failure.SESSION_NOT_FOUND, detail=session_dir)
    text = _read_text(session_file(session_dir), Failure.SESSION_NOT_FOUND)
    fields = _header_block(text, "# Session")
    for required in ("Bridge-Format", "Local", "Peer", "Workflow"):
        if not fields.get(required):
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="SESSION.md has no {0} line".format(required),
            )
    if not os.path.isdir(messages_dir(session_dir)):
        raise BridgeError(
            Failure.SESSION_INVALID, detail=messages_dir(session_dir)
        )
    return SessionRecord(
        directory=session_dir,
        bridge_format=fields["Bridge-Format"],
        local=fields["Local"],
        peer=fields["Peer"],
        workflow=fields["Workflow"],
        project=fields.get("Project") or None,
    )


def _message_names(session_dir: str) -> List[str]:
    directory = messages_dir(session_dir)
    try:
        names = os.listdir(directory)
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))
    return sorted(name for name in names if _SEQUENCE_PATTERN.match(name))


def next_sequence(session_dir: str) -> int:
    """One higher than the highest number on disk, worked out by looking.

    `SESSION.md` is not a numbered message and takes no number. Temporary
    publication files start with a dot and cannot be mistaken for one.
    """
    highest = 0
    for name in _message_names(session_dir):
        match = _SEQUENCE_PATTERN.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def read_sealed_implementation(
    session_dir: str,
) -> Optional[SealedImplementation]:
    """The repository and baseline sealed at implementation start, if any.

    Only the header block of the one `implementation-start` local record is
    read. Returns nothing when the session has not sealed anything yet, which is
    an ordinary state and not a failure.
    """
    for name in _message_names(session_dir):
        if not name.endswith(LOCAL_RECORD_SUFFIX):
            continue
        path = os.path.join(messages_dir(session_dir), name)
        text = _read_text(path, Failure.SESSION_INVALID)
        fields = _header_block(text, "# Message ")
        if fields.get("Record") != "implementation-start":
            continue
        repository = fields.get("Repository-Path")
        roots = fields.get("Repository-Root-Commits")
        baseline = fields.get("Baseline")
        if not repository or not roots or not baseline:
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="incomplete implementation-start record: " + path,
            )
        match = _SEQUENCE_PATTERN.match(name)
        sequence = int(match.group(1)) if match else 0
        return SealedImplementation(
            sequence=sequence,
            repository_path=repository,
            root_commits=tuple(roots.split()),
            baseline=baseline,
        )
    return None

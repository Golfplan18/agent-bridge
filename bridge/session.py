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

The rename is the moment of publication, and the report afterwards says which
side of it something went wrong on. Anything before it means nothing was
published. The one thing that can still fail after it is forcing the folder
entry itself out to the disk, and that is a different fact: the message is
there and complete, but a machine failure could still lose the name. It is
reported as its own failure, naming the file, rather than as "nothing was
published", which would be untrue.

Which side something happened on is settled by looking, not by remembering. A
flag set on the line after the rename is one instruction too late: a signal can
arrive in between, and the report would then say nothing was published while the
message sat on the disk. So the question is asked of the filesystem instead, and
it is asked in the one way that cannot be answered wrongly: the temporary file's
own identity is noted before the rename, and the message counts as published
only when the canonical name now holds that very file. A temporary file that has
merely gone - because the folder it was in went with it - proves nothing and is
not taken for a rename.

Being stopped is not a publication failure and is never dressed up as one. An
interrupt or a termination signal is passed straight on, after the temporary
file has been cleared away if there is one, so that what the person reads is
that they stopped it.

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
from .peer import SignalStop

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


def _rename_happened(path: str, written: Optional[Tuple[int, int]]) -> bool:
    """Did the rename go through? Ask the filesystem, not a variable.

    `written` identifies the temporary file that was filled in - which device
    it is on and which file on that device. A rename moves that exact file onto
    the canonical name without making a new one, so the canonical name holding
    that same identity is the rename having happened, and nothing else is.

    Neither half would do on its own. The canonical name merely existing proves
    nothing, because a replaced plan is written over a file that was already
    there. The temporary file merely being gone proves nothing either, because
    the folder it was in could have gone with it while the rename failed.

    `None` means nothing was ever written, so nothing can have been renamed. A
    filesystem that will not answer is read as "not published", which is the
    cautious way round: the caller is then told to look for a message, rather
    than told to trust one that may not be there.
    """
    if written is None:
        return False
    try:
        found = os.stat(path)
    except OSError:
        return False
    return (found.st_dev, found.st_ino) == written


def publish(path: str, text: str) -> str:
    """Write one canonical file so that it is either whole or absent.

    The text goes into a task-owned temporary file beside the destination, is
    flushed and forced to disk, and only then renamed onto the canonical name.
    Any failure up to and including that rename removes the temporary file and
    reports `PUBLICATION_FAILURE`, leaving the session exactly as it was.

    The rename is the moment of publication, and which side of it something
    happened on is decided by asking whether the canonical name now holds the
    very file that was written, rather than by a variable set afterwards. After
    the rename the message is in place and
    complete, and the only thing left to do is force the folder entry naming it
    out to the disk. If that fails, saying nothing was published would be a lie,
    so it is reported as `PUBLICATION_NOT_FLUSHED` instead: the message is
    there, and a machine failure could still lose it. The turn fails either way.

    An interrupt or a stop signal is not a publication failure. It is raised on
    unchanged - after the temporary file is cleared away, when the rename had
    not happened - so that nothing reports "nothing was published" about a
    message that is on the disk, and nothing calls a person's own decision to
    stop a fault of this function.
    """
    directory = os.path.dirname(os.path.abspath(path))
    try:
        handle, temp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=directory)
    except OSError as exc:
        raise BridgeError(Failure.PUBLICATION_FAILURE, detail=str(exc))
    written = None  # type: Optional[Tuple[int, int]]
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            marks = os.fstat(stream.fileno())
            written = (marks.st_dev, marks.st_ino)
        os.replace(temp_path, path)
    except BaseException as exc:
        if _rename_happened(path, written):
            # The message is in place. Whatever went wrong went wrong after
            # publication, so it is never reported as though nothing was sent.
            if isinstance(exc, (SignalStop, KeyboardInterrupt)):
                raise
            raise BridgeError(
                Failure.PUBLICATION_NOT_FLUSHED,
                detail="{0}: {1}".format(path, exc),
            )
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        if isinstance(exc, (SignalStop, KeyboardInterrupt)):
            raise
        raise BridgeError(
            Failure.PUBLICATION_FAILURE, detail="{0}: {1}".format(path, exc)
        )
    try:
        _fsync_directory(directory)
    except OSError as exc:
        raise BridgeError(
            Failure.PUBLICATION_NOT_FLUSHED,
            detail="{0}: {1}".format(path, exc),
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


def local_to_peer_text(
    sequence: int,
    local: str,
    peer: str,
    body: str,
    review_evidence: Optional[str] = None,
) -> str:
    """A request going out to the peer, and where its evidence was.

    An ordinary request carries nothing but who it is from and who it is for.
    A review request carries one more runner-owned line, `Review-Evidence:`,
    naming the exact file the reviewing peer was given to read.

    That line is a note the runner writes to itself and to whoever reads the
    session afterwards: this is the file this turn generated. It is not what
    gets the file to the peer, and the peer never sees it - a header line lives
    above the body, and the peer receives only the body. What tells a peer where
    the evidence is are the connector's restriction switches, and what proves it
    read the file is the token inside the file coming back in the answer.

    None of the `Review-Request`, `Review-Base` or `Review-Head` fields ever
    appear on a request. Those bind an answer, and no answer has been given
    yet.
    """
    header_lines = ["From: {0}".format(local), "To: {0}".format(peer)]
    if review_evidence:
        header_lines.append("Review-Evidence: {0}".format(review_evidence))
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
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

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

**And that question has three answers, not two.** Yes, the canonical name holds
the very file that was written. No, it does not - it is absent, or it holds
something else, or nothing was ever written. Or the filesystem would not answer
at all, which is neither: something went wrong while the message was being moved
into place and the name could not then be examined, so there is genuinely no
telling. That third answer used to be folded into "no", which meant a message
that really was in place could be reported as never sent, with advice to run the
command again. It is now its own failure, `PUBLICATION_UNCERTAIN`, which names
the file and asks the person to look.

Being stopped is not a publication failure and is never dressed up as one. An
interrupt or a termination signal is passed straight on, after the temporary
file has been cleared away if there is one, so that what the person reads is
that they stopped it. The signal handlers are on for the whole life of that
file - from before its name is made until it has either been renamed into place
or removed - so there is no instant at which a stop can end the process while
the file exists and nothing is arranging to clear it away. Across that whole
stretch a stop is written down rather than raised where it lands, and raised
once the file is gone. There is one window inside it where a stop does raise
immediately - the stretch that fills the file in and renames it - and that
window is opened inside the very handling that clears the file away, so a stop
raised there is caught, the file goes, and the stop then carries on as itself.
Either way, being stopped never becomes a way to leave a file behind. Two
things outrank saying so, in that order. A temporary file that could not be
removed is reported as `CLEANUP_FAILURE` even when what went wrong was the
person pressing a key, because the leftover file is the thing they actually
have to go and deal with and nothing else would tell them it is there. And not
knowing whether the rename happened is reported as `PUBLICATION_UNCERTAIN`,
because "you stopped it" would be read as "so nothing happened", which is
exactly the untruth that failure exists to avoid.

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
from .peer import SignalStop, stopped_by_signal

SESSION_FILENAME = "SESSION.md"
MESSAGES_DIRNAME = "messages"

#: Every temporary file this module creates while publishing carries this
#: prefix, so a check can assert that none of them survived a failure.
TEMP_PREFIX = ".agent-bridge-publish-"

#: The three canonical message filename shapes.
INITIATOR_TO_PEER_SUFFIX = "-initiator-to-peer.md"
PEER_TO_INITIATOR_SUFFIX = "-peer-to-initiator.md"
INITIATOR_RECORD_SUFFIX = "-initiator-record.md"

BODY_HEADING = "## Body"

_SEQUENCE_PATTERN = re.compile(r"^(\d{4,})-")
_INITIATOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)


class SessionRecord(NamedTuple):
    """What `SESSION.md` said, read fresh from disk.

    Written once when the session is created and never changed afterwards, so
    nothing in here can go stale: the calling application's inert label, the
    target it selected, and the project when there is one.
    """

    directory: str
    bridge_format: str
    initiator: str
    peer: str
    project: Optional[str]


# -- paths ------------------------------------------------------------------


def session_file(session_dir: str) -> str:
    return os.path.join(session_dir, SESSION_FILENAME)


def messages_dir(session_dir: str) -> str:
    return os.path.join(session_dir, MESSAGES_DIRNAME)


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


def _rename_outcome(
    path: str, written: Optional[Tuple[int, int]]
) -> Optional[bool]:
    """Did the rename go through? Ask the filesystem, not a variable.

    `written` identifies the temporary file that was filled in - which device
    it is on and which file on that device. A rename moves that exact file onto
    the canonical name without making a new one, so the canonical name holding
    that same identity is the rename having happened, and nothing else is.

    Neither half would do on its own. The canonical name merely existing proves
    nothing, because another process could have created a file there. The
    temporary file merely being gone proves nothing either, because the folder
    it was in could have gone with it while the rename failed.

    There are three answers, and the third one is the point of this shape:

    - `True` - the canonical name holds the very file that was written, so the
      message is published.
    - `False` - nothing was ever written, or the canonical name does not exist,
      or it holds some other file. Not published.
    - `None` - the canonical name could not be examined at all. There is no
      telling either way, and saying "not published" about it would be a guess
      dressed up as a fact.

    **Test this result with `is True`, `is None` and `is False`, never for
    truthiness.** `None` is falsey, so `if not outcome` quietly folds "there is
    no telling" into "it did not happen" - which is the exact mistake this
    return type exists to prevent, and the reason it is worth the awkwardness of
    a three-valued answer.
    """
    if written is None:
        return False
    try:
        found = os.stat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return (found.st_dev, found.st_ino) == written


def publish(path: str, text: str) -> str:
    """Write one canonical file so that it is either whole or absent.

    The text goes into a task-owned temporary file beside the destination, is
    flushed and forced to disk, and only then renamed onto the canonical name.
    In the ordinary case a failure before that rename removes the temporary
    file and reports `PUBLICATION_FAILURE`, leaving the session exactly as it
    was - but that is only the ordinary case, and the exact ordering, including
    where it does not hold, is set out below.

    The rename is the moment of publication, and which side of it something
    happened on is decided by asking whether the canonical name now holds the
    very file that was written, rather than by a variable set afterwards. After
    the rename the message is in place and
    complete, and the only thing left to do is force the folder entry naming it
    out to the disk. If that fails, saying nothing was published would be a lie,
    so it is reported as `PUBLICATION_NOT_FLUSHED` instead: the message is
    there, and a machine failure could still lose it. The turn fails either way.

    When something goes wrong, four things are decided in this order, and the
    order is what makes each report true.

    First, if the message is in place, that is the report - the failure happened
    after publication and is never dressed up as nothing having been sent. A
    stop arriving there is raised as itself.

    Otherwise the temporary file has to go, whether or not the rename is
    knowable. A file that is already gone is the wanted state and passes in
    silence; a removal that fails any other way is `CLEANUP_FAILURE`, naming the
    leftover file, and it outranks everything else here - including re-raising a
    stop - because a leftover file is the thing a person actually has to go and
    deal with, and nothing else would tell them it is there.

    Then, if the canonical name could not be examined, the report is
    `PUBLICATION_UNCERTAIN`: there is no telling whether the message arrived, so
    the person is asked to look. This one outranks re-raising a stop too, and
    for a sharper reason: "you stopped it" would be read as "so nothing
    happened", and not knowing whether anything happened is precisely what is
    being reported.

    Only then is an interrupt or a stop signal raised on unchanged - whether it
    interrupted the writing or arrived while the temporary file was being
    removed - so that nothing calls a person's own decision to stop a fault of
    this function; and only then, in the ordinary case, is
    `PUBLICATION_FAILURE` raised, which truthfully says nothing was published.

    All of that is guarded by signal handlers covering the whole life of the
    temporary file, and the life of that file is exactly what they have to
    cover. They go on before its name is made and stay on until it has either
    been renamed into place or cleared away, so there is no instruction at which
    a termination can end the process outright, skip the handling above, and
    leave a `.agent-bridge-publish-` file in somebody's session folder.

    Within that cover a stop is deferred - written down rather than raised -
    for the whole life of the file, and exactly one window is opened where it
    raises where it lands: the stretch that fills the file in and renames it.
    This is the same arrangement as `run_bounded` in `bridge.peer`, and for the
    same reason. Making the name is deferred because until there is a name the
    handling above would have nothing to clear away, and the handling itself is
    deferred because a second stop landing in the middle of it would abandon
    the tidying half done. The window is safe precisely because it sits inside
    the `try` whose `except` does that tidying: a stop raised in it does not end
    the process where it stands, it goes to the handling above, which removes
    the file and then passes the stop on as itself. A stop that arrived before
    the window opened is raised as the window opens, inside that same `try`, so
    it is cleared up the same way; and leaving the window puts the deferral
    back however it is left. There is therefore no instruction between the file
    appearing and the file being gone at which a stop can be raised out of
    reach of the tidying.

    Forcing the folder entry out to the disk is deliberately outside all of
    that. By then the message is in place, the temporary file is gone and there
    is nothing left to abandon, so a stop written down during the deferred
    stretch is raised first, and a stop arriving during the flush itself raises
    where it lands - which it must, because after the flush nothing would ever
    read a written-down stop back and it would simply be lost.
    """
    directory = os.path.dirname(os.path.abspath(path))
    with stopped_by_signal() as watch:
        written = None  # type: Optional[Tuple[int, int]]
        # A stop is written down for the whole life of the temporary file -
        # while its name is being made, while it is being filled in and renamed,
        # and while the handling below is clearing it away - and exactly one
        # window is opened where a stop raises where it lands. Making the name
        # has to be deferred because until there is a name the handling below
        # would have nothing to clear away; the handling itself has to be
        # deferred because a second stop landing inside it would abandon the
        # tidying half done. The one window sits inside the `try` whose `except`
        # does that tidying, so there is no instruction between the file
        # appearing and the file being gone at which a stop can leave without
        # the tidying having run.
        with watch.deferring():
            try:
                handle, temp_path = tempfile.mkstemp(
                    prefix=TEMP_PREFIX, dir=directory
                )
            except OSError as exc:
                raise BridgeError(Failure.PUBLICATION_FAILURE, detail=str(exc))
            try:
                # The window. A stop that arrived while the name was being made
                # is raised as this opens, which is what stops a turn already
                # told to stop from going on and writing the message anyway -
                # and it is raised here, inside the `except` below, so the file
                # is cleared away just as it would be for a stop that landed in
                # the middle of the writing. Leaving the window puts the
                # deferral back however it is left, so the handling always runs
                # written-down.
                with watch.allowing():
                    with os.fdopen(handle, "w", encoding="utf-8") as stream:
                        stream.write(text)
                        stream.flush()
                        os.fsync(stream.fileno())
                        marks = os.fstat(stream.fileno())
                        written = (marks.st_dev, marks.st_ino)
                    os.replace(temp_path, path)
            except BaseException as exc:
                # Stops are written down for the whole of the handling, not just
                # for the removal inside it, so a termination or an interrupt
                # landing anywhere in here cannot abandon the tidying half done
                # and leave the file on the disk with nothing saying so.
                # Anything written down is raised below, once there is nothing
                # left to abandon and the two reports that outrank it have had
                # their turn.
                outcome = _rename_outcome(path, written)
                if outcome is True:
                    # The message is in place. Whatever went wrong went wrong
                    # after publication, so it is never reported as though
                    # nothing was sent.
                    if isinstance(exc, (SignalStop, KeyboardInterrupt)):
                        raise
                    raise BridgeError(
                        Failure.PUBLICATION_NOT_FLUSHED,
                        detail="{0}: {1}".format(path, exc),
                    )
                # Not published, or not knowably published. Either way the
                # temporary file must not be left behind, and a removal that
                # fails is the loudest thing here: nothing else would tell
                # anybody the file exists.
                removal = None  # type: Optional[OSError]
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                except OSError as failed:
                    removal = failed
                if removal is not None:
                    raise BridgeError(
                        Failure.CLEANUP_FAILURE,
                        detail=(
                            "the temporary file {0} could not be removed after "
                            "publishing {1} failed ({2}): {3}".format(
                                temp_path, path, exc, removal
                            )
                        ),
                    )
                if outcome is None:
                    # The rename may have happened. Reporting a stop, or
                    # reporting that nothing was published, would both be
                    # claims nobody can make.
                    raise BridgeError(
                        Failure.PUBLICATION_UNCERTAIN,
                        detail="{0}: {1}".format(path, exc),
                    )
                # A stop that arrived while the temporary file was being
                # removed is raised here, in the same place and at the same
                # rank as one that interrupted the writing: after the two
                # reports a person has to act on, and before anything that
                # would call their own decision to stop a fault of this
                # function.
                watch.raise_if_stopped()
                if isinstance(exc, (SignalStop, KeyboardInterrupt)):
                    raise
                raise BridgeError(
                    Failure.PUBLICATION_FAILURE,
                    detail="{0}: {1}".format(path, exc),
                )
        # Nothing is left to abandon here: the message is in place and the
        # temporary file is gone. So a stop written down during that stretch is
        # raised now, and the flush below runs outside the deferral - a stop
        # arriving during it has to raise where it lands, because after the
        # flush nothing would ever read a written-down stop back and it would
        # simply be lost.
        watch.raise_if_stopped()
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
    """The body exactly as supplied, including its final-newline choice."""
    return body


def _compose(title: str, header_lines: Sequence[str], body: str) -> str:
    """Title, then the header block, then the body under its own heading."""
    header = "".join(line + "\n" for line in header_lines)
    return "{0}\n{1}\n{2}\n\n{3}".format(
        title, header, BODY_HEADING, body_block(body)
    )


def session_text(
    initiator: str,
    peer: str,
    body: str,
    project: Optional[str] = None,
) -> str:
    """`SESSION.md`, written once and never edited."""
    header_lines = [
        "Bridge-Format: {0}".format(BRIDGE_FORMAT),
        "Initiator: {0}".format(initiator),
        "Peer: {0}".format(peer),
    ]
    if project:
        header_lines.append("Project: {0}".format(project))
    return "# Session\n" + _compose("", header_lines, body)


def initiator_to_peer_text(
    sequence: int,
    initiator: str,
    peer: str,
    body: str,
) -> str:
    """A request going out to the peer.

    A request carries nothing but who it is from and who it is for.
    """
    header_lines = ["From: {0}".format(initiator), "To: {0}".format(peer)]
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


def peer_to_initiator_text(
    sequence: int,
    peer: str,
    initiator: str,
    body: str,
) -> str:
    """A peer's final answer, copied through unchanged."""
    header_lines = ["From: {0}".format(peer), "To: {0}".format(initiator)]
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


def initiator_record_text(
    sequence: int,
    kind: str,
    initiator: str,
    body: str,
) -> str:
    """An application-neutral note: no recipient and no interpreted fields."""
    header_lines = ["Record: {0}".format(kind), "From: {0}".format(initiator)]
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


# -- cold reading -----------------------------------------------------------


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _session_fields(text: str) -> Dict[str, str]:
    """Parse only the fixed Format 2 session envelope.

    The parser accepts the writer's exact structure, rejects duplicate and
    unknown fields, and stops structurally before the inert body. Text below
    `## Body` can therefore look like headers without changing the session.
    """
    lines = _normalise(text).split("\n")
    if not lines or lines[0] != "# Session":
        raise BridgeError(
            Failure.SESSION_INVALID, detail="SESSION.md must start with # Session"
        )
    if len(lines) < 7 or lines[1] != "":
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="SESSION.md does not have the Format 2 header layout",
        )

    fields = {}  # type: Dict[str, str]
    allowed = {"Bridge-Format", "Initiator", "Peer", "Project"}
    index = 2
    while index < len(lines) and lines[index] != "":
        line = lines[index]
        name, separator, value = line.partition(":")
        if not separator or name not in allowed or not value.startswith(" "):
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="invalid Format 2 session header: {0!r}".format(line),
            )
        if name in fields:
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="SESSION.md repeats the {0} field".format(name),
            )
        parsed = value[1:]
        if not parsed or parsed != parsed.strip():
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="SESSION.md has an invalid {0} value".format(name),
            )
        fields[name] = parsed
        index += 1

    if index + 2 >= len(lines):
        raise BridgeError(
            Failure.SESSION_INVALID, detail="SESSION.md has no body"
        )
    if lines[index : index + 3] != ["", BODY_HEADING, ""]:
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="SESSION.md does not have the Format 2 body layout",
        )
    body = "\n".join(lines[index + 3 :])
    if not body.strip():
        raise BridgeError(
            Failure.SESSION_INVALID, detail="SESSION.md has an empty body"
        )
    return fields


def validate_initiator(label: str, failure: Failure = Failure.USAGE_ERROR) -> str:
    """Return an inert ASCII initiator slug, or reject it in the caller's terms."""
    if not _INITIATOR_PATTERN.fullmatch(label):
        raise BridgeError(
            failure,
            detail="initiator must be an ASCII slug beginning with a letter or digit",
        )
    return label


def _read_text(path: str, missing: Failure) -> str:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except FileNotFoundError:
        raise BridgeError(missing, detail=path)
    except UnicodeDecodeError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))


def read_session(session_dir: str) -> SessionRecord:
    """Read `SESSION.md` back from disk, every time it is needed."""
    if not os.path.isdir(session_dir):
        raise BridgeError(Failure.SESSION_NOT_FOUND, detail=session_dir)
    text = _read_text(session_file(session_dir), Failure.SESSION_NOT_FOUND)
    fields = _session_fields(text)
    for required in ("Bridge-Format", "Initiator", "Peer"):
        if not fields.get(required):
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="SESSION.md has no {0} line".format(required),
            )
    if fields["Bridge-Format"] != str(BRIDGE_FORMAT):
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="unsupported Bridge-Format: {0}".format(
                fields["Bridge-Format"]
            ),
        )
    validate_initiator(fields["Initiator"], failure=Failure.SESSION_INVALID)
    from .connectors import HARNESS_IDS

    if fields["Peer"] not in HARNESS_IDS:
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="SESSION.md names an unsupported peer: {0}".format(fields["Peer"]),
        )
    project = fields.get("Project") or None
    if project is not None and not os.path.isabs(project):
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="SESSION.md Project is not absolute: {0}".format(project),
        )
    if not os.path.isdir(messages_dir(session_dir)):
        raise BridgeError(
            Failure.SESSION_INVALID, detail=messages_dir(session_dir)
        )
    return SessionRecord(
        directory=session_dir,
        bridge_format=fields["Bridge-Format"],
        initiator=fields["Initiator"],
        peer=fields["Peer"],
        project=project,
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

"""The one internal list of ways an Agent Bridge turn can fail.

This enumeration is implementation-internal. It is not a public protocol, not a
wire format, and not a third-party compatibility surface: nothing outside this
repository should depend on the member names or on their spelling, and they may
be renamed whenever the code needs it.

Its purpose is narrow. Connectors translate whatever a vendor's command-line
program did into exactly one of these members and may not invent private codes
of their own. Only the runner turns a member into words, using `render()` below,
so every failure reaches a person the same way: what happened, and the one thing
to do next.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import enum
from typing import Dict, Optional, Tuple


class Failure(enum.Enum):
    """Every way an Agent Bridge turn is allowed to fail."""

    # Reaching and qualifying a peer harness.
    MISSING_CLI = "MISSING_CLI"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    UNREPORTABLE_VERSION = "UNREPORTABLE_VERSION"
    UNQUALIFIED_VERSION = "UNQUALIFIED_VERSION"
    UNQUALIFIED_PLATFORM = "UNQUALIFIED_PLATFORM"
    RESTRICTIONS_UNAVAILABLE = "RESTRICTIONS_UNAVAILABLE"
    QUALIFICATION_UNSAFE_OR_INCONCLUSIVE = "QUALIFICATION_UNSAFE_OR_INCONCLUSIVE"

    # Running one bounded turn.
    BUSY_SESSION = "BUSY_SESSION"
    TIMEOUT = "TIMEOUT"
    PEER_FAILURE = "PEER_FAILURE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    CLEANUP_FAILURE = "CLEANUP_FAILURE"

    # Calling the commands correctly.
    USAGE_ERROR = "USAGE_ERROR"
    UNKNOWN_HARNESS = "UNKNOWN_HARNESS"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"
    UNKNOWN_RECORD_KIND = "UNKNOWN_RECORD_KIND"

    # Reading and writing the session record.
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_INVALID = "SESSION_INVALID"
    SESSION_EXISTS = "SESSION_EXISTS"
    PUBLICATION_FAILURE = "PUBLICATION_FAILURE"
    PUBLICATION_NOT_FLUSHED = "PUBLICATION_NOT_FLUSHED"
    PUBLICATION_UNCERTAIN = "PUBLICATION_UNCERTAIN"


# For each failure: what happened, then the single next action.
_GUIDANCE: Dict[Failure, Tuple[str, str]] = {
    Failure.MISSING_CLI: (
        "The peer harness's command-line program could not be found on this "
        "computer.",
        "Install that harness's official command-line program, make sure it is "
        "on PATH, and run the readiness check again.",
    ),
    Failure.AUTHENTICATION_REQUIRED: (
        "The peer harness is installed but is not signed in.",
        "Sign in using that harness's own login command, then run the readiness "
        "check again.",
    ),
    Failure.UNREPORTABLE_VERSION: (
        "The peer harness's command-line program did not print a version that "
        "could be read, so there is no way to tell whether it is a tested one.",
        "Run that program's version command by hand; if it still prints nothing "
        "readable, this harness cannot be qualified and must not be used on a "
        "real project.",
    ),
    Failure.UNQUALIFIED_VERSION: (
        "The installed version of the peer harness is outside the versions this "
        "connector has actually been tested against.",
        "Install a tested version, or rerun the disposable qualification "
        "against the installed version and update the connector's declaration "
        "in source.",
    ),
    Failure.UNQUALIFIED_PLATFORM: (
        "This operating system family or major version is outside the coverage "
        "this connector has actually been tested on.",
        "Use a tested platform, or rerun the disposable qualification there and "
        "update the connector's declaration in source.",
    ),
    Failure.RESTRICTIONS_UNAVAILABLE: (
        "The peer harness does not offer the exact switches Agent Bridge needs "
        "to make it unable to write project files, change Git state, or reach "
        "a browser, the web, MCP, messaging, credentials, publication or "
        "deployment - whether by removing those tools or by confining them in "
        "an enforced sandbox.",
        "Do not give this harness real project access; report the missing "
        "restriction so the connector's declaration can be corrected.",
    ),
    Failure.QUALIFICATION_UNSAFE_OR_INCONCLUSIVE: (
        "The disposable qualification run did not clearly prove the harness "
        "stayed inside its approved boundary.",
        "Read the reported synthetic-repository path, work out what happened, "
        "and rerun qualification; do not use this harness on a real project "
        "until it passes.",
    ),
    Failure.BUSY_SESSION: (
        "Another Agent Bridge turn is already holding this session's lock.",
        "Wait for that turn to finish and run the command again; nothing in the "
        "session was changed.",
    ),
    Failure.TIMEOUT: (
        "The turn reached its deadline before the peer produced an answer.",
        "Inspect the visible session and target state before deciding whether "
        "to retry with a longer --timeout.",
    ),
    Failure.PEER_FAILURE: (
        "The peer harness's program exited with a failure.",
        "Read the peer's own error output, fix the problem inside that harness, "
        "then run the turn again.",
    ),
    Failure.EMPTY_RESPONSE: (
        "The peer produced no text at all, so there is nothing to publish.",
        "Check the peer harness by hand, then run the turn again.",
    ),
    Failure.CLEANUP_FAILURE: (
        "A file or process this turn created could not be removed, so the turn "
        "cannot be called finished.",
        "Remove the reported path or process by hand and confirm nothing of "
        "this turn is still running before continuing.",
    ),
    Failure.USAGE_ERROR: (
        "The command was called with missing, conflicting, or empty arguments.",
        "Correct the command line, including the text supplied on standard "
        "input, and run it again.",
    ),
    Failure.UNKNOWN_HARNESS: (
        "The named target is not one of the four Agent Bridge knows about.",
        "Name one of: codex, claude, zcode, hermes.",
    ),
    Failure.CONNECTOR_UNAVAILABLE: (
        "That harness identifier is real, but this build ships no connector "
        "able to call it.",
        "Use a harness whose connector ships in this build.",
    ),
    Failure.UNKNOWN_RECORD_KIND: (
        "That record kind is not one of the two the record command accepts.",
        "Name one of: session-create, note.",
    ),
    Failure.SESSION_NOT_FOUND: (
        "There is no session record at the given directory.",
        "Create the session first with record --kind session-create, or correct "
        "the --session path.",
    ),
    Failure.SESSION_INVALID: (
        "The session directory exists, but its record could not be read as a "
        "valid session.",
        "Open the session folder and inspect SESSION.md and messages/; repair "
        "it, or start a new session.",
    ),
    Failure.SESSION_EXISTS: (
        "A session record already exists at that directory, and creating "
        "another one there would overwrite it.",
        "Continue in the existing session, or choose a new empty --session "
        "directory.",
    ),
    Failure.PUBLICATION_FAILURE: (
        "The message could not be written in full and moved into place, so "
        "nothing was published and the record is unchanged.",
        "Check that the session directory is writable and has free space, then "
        "run the command again.",
    ),
    Failure.PUBLICATION_NOT_FLUSHED: (
        "The message was written and moved into place, so it is published, but "
        "the folder entry naming it could not be forced onto the disk, so a "
        "machine failure could still lose it.",
        "Confirm the reported file is there and readable, and treat this turn "
        "as unfinished until the session directory's disk is behaving.",
    ),
    Failure.PUBLICATION_UNCERTAIN: (
        "Something went wrong while the message was being moved into place, "
        "and the canonical name could not then be examined, so there is no "
        "telling whether the message reached it.",
        "Look in the session's messages folder for the reported file before "
        "you do anything else: if it is there, the message is complete and "
        "the writing is finished; if it is absent, the message never arrived. "
        "Do not run the command again until you know which of the two it is.",
    ),
}


def render(failure: Failure, detail: Optional[str] = None) -> str:
    """Turn one failure into the message a person reads.

    The result is always the same two things in the same order: what happened,
    then the single next action. `detail` is optional observed fact - a path, an
    observed version, a process id - added in parentheses after the reason.
    """
    reason, next_action = _GUIDANCE[failure]
    if detail:
        reason = "{0} ({1})".format(reason, detail)
    return "{0} Next action: {1}".format(reason, next_action)


class BridgeError(Exception):
    """One failure, carried out to whoever renders the message.

    Holds the failure member and any observed detail worth showing, so the
    runner can print one actionable message and choose an exit status without
    inspecting exception text.
    """

    def __init__(self, failure: Failure, detail: Optional[str] = None) -> None:
        super().__init__(render(failure, detail))
        self.failure = failure
        self.detail = detail

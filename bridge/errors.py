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
    INVALID_VERDICT = "INVALID_VERDICT"

    # Binding a review to the exact code that was reviewed.
    REPOSITORY_CHANGED = "REPOSITORY_CHANGED"
    BASELINE_CHANGED = "BASELINE_CHANGED"
    HEAD_CHANGED = "HEAD_CHANGED"
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
    PLAN_SEALED = "PLAN_SEALED"
    IMPLEMENTATION_ALREADY_SEALED = "IMPLEMENTATION_ALREADY_SEALED"
    NO_IMPLEMENTATION_BASELINE = "NO_IMPLEMENTATION_BASELINE"
    PUBLICATION_FAILURE = "PUBLICATION_FAILURE"

    # Reading the project repository.
    REPOSITORY_UNREADABLE = "REPOSITORY_UNREADABLE"
    DIRTY_WORKTREE = "DIRTY_WORKTREE"
    BASELINE_NOT_ANCESTOR = "BASELINE_NOT_ANCESTOR"


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
        "to deny project writes and outside reads.",
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
        "Run the turn again with a longer --timeout, or check the peer harness "
        "by hand first.",
    ),
    Failure.PEER_FAILURE: (
        "The peer harness's program exited with a failure.",
        "Read the peer's own error output, fix the problem inside that harness, "
        "then run the turn again.",
    ),
    Failure.EMPTY_RESPONSE: (
        "The peer produced no text at all, so there is nothing to publish.",
        "Check the peer harness by hand, then run the turn again; Git stays "
        "locked in the meantime.",
    ),
    Failure.INVALID_VERDICT: (
        "The review response did not end with one of the three exact verdict "
        "lines, so it is not a decision Agent Bridge can act on. The peer's "
        "text was kept as an ordinary message carrying no review authority.",
        "Read the kept message if it is useful, then run the review again; "
        "this is a technical error, never an acceptance, and Git stays locked "
        "until a fresh review returns an exact ACCEPT.",
    ),
    Failure.REPOSITORY_CHANGED: (
        "The repository in play is not the one sealed when implementation "
        "started.",
        "Point the turn at the sealed repository, or start a new session for "
        "the different repository.",
    ),
    Failure.BASELINE_CHANGED: (
        "The review baseline is not the baseline commit sealed when "
        "implementation started.",
        "Run the review again using the sealed baseline commit.",
    ),
    Failure.HEAD_CHANGED: (
        "The task branch moved, so the review no longer describes the code that "
        "is actually there.",
        "Run a fresh review against the current head; the earlier verdict no "
        "longer applies to it.",
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
        "The named harness is not one of the five Agent Bridge knows about.",
        "Name one of: codex, claude, zcode, hermes, minimax-code.",
    ),
    Failure.CONNECTOR_UNAVAILABLE: (
        "That harness identifier is real, but this build ships no connector "
        "able to call it.",
        "Use a harness whose connector ships in this build.",
    ),
    Failure.UNKNOWN_RECORD_KIND: (
        "That record kind is not one of the six the record command accepts.",
        "Name one of: session-create, user-correction, plan-approval, "
        "technical-error, implementation-start, user-waiver.",
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
    Failure.PLAN_SEALED: (
        "This session already holds an approved PLAN.md, and sealing another "
        "one would overwrite an approved plan.",
        "If the user has approved a replacement plan, record it again with "
        "--replace; otherwise leave the sealed plan alone.",
    ),
    Failure.IMPLEMENTATION_ALREADY_SEALED: (
        "This session already sealed a repository and baseline when "
        "implementation started, and that pairing cannot be changed.",
        "Continue against the sealed repository and baseline, or start a new "
        "session for different work.",
    ),
    Failure.NO_IMPLEMENTATION_BASELINE: (
        "This session has no implementation-start record, so there is no sealed "
        "repository and baseline to bind a review or a waiver to.",
        "Record implementation-start with the project and baseline commit "
        "before requesting a review or recording a waiver.",
    ),
    Failure.PUBLICATION_FAILURE: (
        "The message could not be written in full and moved into place, so "
        "nothing was published and the record is unchanged.",
        "Check that the session directory is writable and has free space, then "
        "run the command again.",
    ),
    Failure.REPOSITORY_UNREADABLE: (
        "The given project directory could not be read as a Git repository.",
        "Correct the --project path so it points at a Git repository you can "
        "read.",
    ),
    Failure.DIRTY_WORKTREE: (
        "The task worktree has uncommitted changes, so there is no exact "
        "committed head for a reviewer to judge.",
        "Commit or set aside the outstanding changes, then run the review "
        "again.",
    ),
    Failure.BASELINE_NOT_ANCESTOR: (
        "The baseline commit does not come before a different task head on the "
        "same history, so a cumulative diff would not describe the work.",
        "Check --review-base and --review-head; the baseline must be an "
        "ancestor of a distinct head.",
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

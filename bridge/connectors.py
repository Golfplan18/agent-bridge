"""The five harnesses Agent Bridge can call, and how one call is described.

Agent Bridge knows exactly five coding-agent harnesses, named once here and
never discovered at runtime. Looking one up is a literal five-way switch: there
is no registry, no plugin search, no dynamic import, and no base class for a
connector to inherit. Adding a sixth harness means editing this file, which is
the point - the set of programs the bridge will start is visible in source.

Three connector modules ship in this build. Codex and Claude Code each offer
exactly two operations - answer whether the harness could be used right now,
and compose the one fixed argument vector a turn runs. ZCode's module runs the
same prerequisites and then refuses both, because its installed build cannot
take the message on standard input; its own docstring holds the evidence. The
other two branches resolve to nothing, so asking for one is an honest failure
rather than a silent fallback.

This module also holds what the two connectors share, and the sharing is
deliberately shallow: a handful of plain functions, called by both, and no
inheritance. Finding the program, reading its version, describing this
computer, running one of a harness's own cheap questions inside the turn's
deadline, and proving that a restriction switch really exists on the installed
version are the same work whichever harness is being asked, so they are written
once. Everything that differs between two harnesses - which switches, which
questions, what the answers mean - stays in that harness's own module, where a
reader can see the whole of it in one place.

Finally it holds the two small fixed shapes the rest of the code passes around:
what a connector claims to have been tested against, and what one bounded call
to a peer program consists of.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from typing import Any, NamedTuple, Optional, Sequence, Tuple

from .errors import BridgeError, Failure
from .peer import CompletedCall, Deadline, run_bounded

#: The five harness identifiers, fixed and ordered. Nothing else is a harness.
HARNESS_IDS: Tuple[str, ...] = (
    "codex",
    "claude",
    "zcode",
    "hermes",
    "minimax-code",
)


class Qualification(NamedTuple):
    """What one connector claims to have actually been tested against.

    This is a source-controlled declaration, not a record of anything observed
    on this machine and not something the bridge writes down between runs. A
    readiness check compares what the installed program reports with these
    values; disagreement is reported, never inferred away.

    `versions` lists exact tested versions. A range belongs here only when
    evidence covers every release inside it. `architectures` is left empty when
    the restriction surface does not depend on the processor. `restrictions` is
    the fixed set of switches that must be present for the harness to be called
    at all - the ones that make the peer unable to write project files, unable
    to alter Git state, and unable to cause a browser, web-fetch, MCP,
    messaging, credential, publication or deployment effect.

    Those are properties, not an inventory. A connector may reach them by
    removing the tools, by confining them in the harness's own enforced
    permission or sandbox mode, or by both, and qualification proves the
    resulting behaviour rather than checking off a list of absent tools. A
    harness that leaves a shell or Git harmlessly available is not thereby
    unqualified; one whose peer can mutate anything or reach outside is.
    """

    cli_identity: str
    versions: Tuple[str, ...]
    os_family: str
    os_major_versions: Tuple[str, ...]
    architectures: Tuple[str, ...]
    restrictions: Tuple[str, ...]


class PeerCommand(NamedTuple):
    """One bounded call to a peer harness's program.

    `argv` is a fixed argument vector, run without a shell. `cwd` is the exact
    directory the program starts in. `env` is the complete environment as
    ordered name/value pairs, so the whole value stays immutable; the caller
    turns it into a mapping at the moment it starts the process.

    There is deliberately no prompt field and no command string. The outgoing
    Markdown body always reaches the peer on standard input, so prompt text
    never passes through a command line or a shell.
    """

    argv: Tuple[str, ...]
    cwd: str
    env: Tuple[Tuple[str, str], ...]


# -- what both connectors do the same way -----------------------------------

#: Any dotted release number. Both shipped harnesses print theirs surrounded by
#: wording of their own - `codex-cli 0.147.0`, `2.1.251 (Claude Code)` - and the
#: wording is the vendor's to change, so only the number is read out of it.
_VERSION = re.compile(r"\d+\.\d+\.\d+")


def environment() -> Tuple[Tuple[str, str], ...]:
    """What a peer program inherits: this process's own environment, unchanged.

    Agent Bridge does not compose an environment for a harness. Each one finds
    its own authentication, subscription and settings where it normally does,
    and the bridge has no opinion about any of them. Restriction is done with
    the harness's own documented switches, because that is the only place it can
    be done honestly - a hand-pruned environment would look like a boundary
    without being one.
    """
    return tuple(os.environ.items())


def executable(program: str) -> str:
    """Where a harness's program actually is, or `MISSING_CLI` if it is nowhere.

    The full path is used from then on, so the program a readiness check looked
    at is exactly the program a turn starts, even if `PATH` changes underneath.
    """
    found = shutil.which(program)
    if found is None:
        raise BridgeError(Failure.MISSING_CLI, detail=program)
    return found


def probe(
    argv: Sequence[str], cwd: str, deadline: Deadline
) -> CompletedCall:
    """Ask a harness one of its own cheap questions, inside the turn's deadline.

    A version, a sign-in status, a listing of switches: none of these is a model
    turn and none of them costs anything. They are still programs, so they are
    started the way everything else is - a fixed argument vector, no shell,
    nothing on standard input, the same deadline, and the same cleanup of the
    process group afterwards.
    """
    return run_bounded(
        argv=tuple(argv),
        cwd=cwd,
        env=environment(),
        stdin_text="",
        deadline=deadline,
    )


def qualified_version(printed: str, qualification: Qualification) -> str:
    """The installed version, once it is one this connector was tested against.

    A version that cannot be read at all is a different problem from one that
    can be read and is untested, and the two are reported differently, because
    the thing to do about them is different.
    """
    found = _VERSION.search(printed)
    if found is None:
        raise BridgeError(
            Failure.UNREPORTABLE_VERSION,
            detail="{0} printed {1!r}".format(
                qualification.cli_identity, printed.strip()[:120]
            ),
        )
    version = found.group(0)
    if version not in qualification.versions:
        raise BridgeError(
            Failure.UNQUALIFIED_VERSION,
            detail="{0} {1}; tested against {2}".format(
                qualification.cli_identity,
                version,
                ", ".join(qualification.versions),
            ),
        )
    return version


def qualified_platform(qualification: Qualification) -> str:
    """This computer, once it is one this connector was tested on.

    The family is what Python calls the operating system, so macOS is `Darwin`.
    The major version is macOS's own where there is one, because that is the
    number a person recognises, and the kernel release everywhere else. The
    processor is compared only when the connector named one; a connector leaves
    that list empty when its restriction switches do not depend on the
    processor.
    """
    family = platform.system()
    release = platform.mac_ver()[0] or platform.release()
    major = release.split(".")[0]
    machine = platform.machine()
    described = "{0} {1} {2}".format(family, major, machine)
    if (
        family != qualification.os_family
        or major not in qualification.os_major_versions
    ):
        raise BridgeError(Failure.UNQUALIFIED_PLATFORM, detail=described)
    if qualification.architectures and machine not in (
        qualification.architectures
    ):
        raise BridgeError(Failure.UNQUALIFIED_PLATFORM, detail=described)
    return described


def qualified_restrictions(
    help_text: str, qualification: Qualification
) -> None:
    """Prove every restriction switch this connector passes really exists.

    A switch a new version has dropped or renamed would otherwise be discovered
    at the worst possible moment: in the middle of a real turn, against a real
    project, with the peer already running. Reading the program's own help is
    how its presence is established without spending a model turn.

    Only the switches are looked for, never the values passed with them. A value
    a version no longer accepts makes the program refuse to start and say so,
    which is loud; a switch that is gone is the quiet failure worth catching
    here.
    """
    missing = [
        switch
        for switch in qualification.restrictions
        if switch not in help_text
    ]
    if missing:
        raise BridgeError(
            Failure.RESTRICTIONS_UNAVAILABLE,
            detail="{0} does not offer {1}".format(
                qualification.cli_identity, ", ".join(missing)
            ),
        )


def readiness(
    harness_id: str,
    program: str,
    version: str,
    described_platform: str,
    authentication: str,
) -> str:
    """The one sentence `check` prints when a peer really can be used.

    Written here rather than in each connector so that readiness reads the same
    way whichever harness was asked: where the program is, which version it is,
    what this computer is, and what the harness itself said about being signed
    in.
    """
    return (
        "{0} is ready: version {1} at {2}, on {3}, {4}, and every restriction "
        "switch this connector passes is present in the program's own "
        "help.".format(
            harness_id, version, program, described_platform, authentication
        )
    )


# -- the five-way switch ----------------------------------------------------


def _switch(harness_id: str) -> Optional[Any]:
    """Resolve one identifier to its connector, or raise for an unknown name.

    The branches are written out one by one on purpose: this is the whole list
    of programs Agent Bridge is willing to start. Two of them still resolve to
    nothing, because no connector for those harnesses has been written. ZCode's
    branch resolves to a module that verifies the program and then refuses to
    call it, so the refusal names the exact reason instead of the generic one.

    The three that do ship are imported inside this function for one ordinary
    reason: each of them uses the shapes and the helpers defined above, and a
    module cannot be half-imported into itself. The names are literal and there
    are three of them; nothing is searched for, and nothing is built from a
    string.
    """
    from . import claude, codex, zcode

    if harness_id == "codex":
        return codex
    if harness_id == "claude":
        return claude
    if harness_id == "zcode":
        return zcode
    if harness_id == "hermes":
        return None
    if harness_id == "minimax-code":
        return None
    raise BridgeError(Failure.UNKNOWN_HARNESS, detail=harness_id)


def resolve(harness_id: str) -> Any:
    """Return the connector for one of the five harnesses.

    Raises UNKNOWN_HARNESS for a name that is not one of the five, and
    CONNECTOR_UNAVAILABLE for one of the five whose connector does not ship in
    this build.
    """
    connector = _switch(harness_id)
    if connector is None:
        raise BridgeError(Failure.CONNECTOR_UNAVAILABLE, detail=harness_id)
    return connector

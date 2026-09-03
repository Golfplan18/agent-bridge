"""The six target CLIs Agent Bridge can call, and how one call is described.

Agent Bridge knows exactly six target CLIs, named once here and never
discovered at runtime. Looking one up is a literal six-way switch: there is no
registry, plugin search, name-derived import, or connector base class. Adding a
target means editing this file, which is the point - the set of programs the
bridge will start is visible in source.

Each target has one hand-written module offering exactly two operations:
answer whether the target could be used right now, and compose the one fixed
argument vector a turn runs. ZCode and Hermes have no dependable standard-input
prompt path, so the body reaches them as one bound command-line option. Hermes,
MiniMax, and Qwen are courier-only and receive no project. Qwen alone has a
documented target-side preprocessing exception; Bridge still records and hands
over its original body unchanged.

This module also holds what the connectors share, and the sharing is
deliberately shallow: a handful of plain functions, called by each, and no
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
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from .errors import BridgeError, Failure
from .peer import CompletedCall, Deadline, run_bounded

#: The six target identifiers, fixed and ordered. Nothing else is a target.
HARNESS_IDS: Tuple[str, ...] = (
    "codex",
    "claude",
    "zcode",
    "hermes",
    "minimax",
    "qwen",
)


class Qualification(NamedTuple):
    """What one connector claims to have actually been tested against.

    This is a source-controlled declaration, not a record of anything observed
    on this machine and not something the bridge writes down between runs. A
    readiness check compares what the installed program reports with these
    values; disagreement is reported, never inferred away.

    `versions` lists exact tested versions. A range belongs here only when
    evidence covers every release inside it. `architectures` is left empty when
    the fixed mechanics do not depend on the processor. `restrictions` is the
    set of switches that must be present for the harness to be called at all:
    the connector's one-shot input and output mechanics and every compatible
    control its fixed practical posture relies on.

    This list is not a claim of complete confinement. A connector may remove
    tools, use an enforced sandbox or permission posture, withhold a project,
    or combine them. Any surviving configuration, tool, same-user access, or
    external-effect route is described in a concrete non-blocking warning.
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

    There is deliberately no prompt field and no command string: the body is
    the runner's to hand over, and a connector only says how. `body_argument`
    is `None` for every harness whose program reads standard input, which is
    the required transport wherever one exists, and the runner then writes the
    body there. A connector may set it only for a program that has demonstrably
    no standard-input path for a one-shot prompt, established by probe. The
    runner then appends the body to `argv` as exactly one final argument, with
    this string in front of it, and writes nothing to standard input. The
    string is empty when the vector already ends in a bare `--` and the body
    follows it whole, and it is the option's own attached prefix - `--oneshot=`
    - when the program's parser will not let a bare `--` stand before a single
    option value. Either way nothing in the body can be read as an option. A
    body that would push the process-creation payload - the vector, the
    environment and their overhead together - past what this computer
    supports, less explicit headroom, is refused before any request is
    published, and is never truncated, split or spilled to a file; so is a
    body holding a NUL byte, which no argument can carry. Under either
    transport prompt text never passes through a shell. `warnings` are the
    concrete limits a successful check and run must surface. `response_parser`
    extracts final text when the fixed output is structured. A
    `stdin_body_limit` refuses input before publication when the target would
    otherwise truncate it.
    """

    argv: Tuple[str, ...]
    cwd: str
    env: Tuple[Tuple[str, str], ...]
    body_argument: Optional[str] = None
    warnings: Tuple[str, ...] = ()
    response_parser: Optional[Callable[[str], str]] = None
    stdin_body_limit: Optional[int] = None


class CheckResult(NamedTuple):
    """One successful readiness sentence and its informational warnings."""

    message: str
    warnings: Tuple[str, ...] = ()


#: The most a body may be when it travels on the command line, in encoded
#: bytes. Fixed by the plan; half the argument space macOS allows a process.
COMMAND_LINE_BODY_LIMIT = 524288

#: Room left unused below the operating system's argument-space limit, because
#: the exact accounting of pointers and padding is the kernel's, not ours.
ARGUMENT_SPACE_HEADROOM = 65536

#: What to assume for the argument space when the operating system will not
#: say: the macOS value, which is the platform the connectors are qualified on.
ASSUMED_ARGUMENT_SPACE = 1048576


def argument_space_limit() -> int:
    """How many bytes of arguments and environment a new process may carry.

    Asked of the operating system where it answers, with the headroom above
    taken off, because a program refused at creation for an oversized argument
    block is a failure that should have been refused here, in words, first.
    """
    try:
        total = os.sysconf("SC_ARG_MAX")
    except (AttributeError, OSError, ValueError):
        total = ASSUMED_ARGUMENT_SPACE
    if not isinstance(total, int) or total <= 0:
        total = ASSUMED_ARGUMENT_SPACE
    return max(0, total - ARGUMENT_SPACE_HEADROOM)


def argument_space_used(
    argv: Sequence[str], env: Iterable[Tuple[str, str]]
) -> int:
    """The bytes a process creation would spend on these arguments and this
    environment: every string with its terminator, and a pointer for each."""
    pointer = 8
    used = 0
    count = 0
    for argument in argv:
        used += len(argument.encode("utf-8")) + 1
        count += 1
    for name, value in env:
        used += len(name.encode("utf-8")) + len(value.encode("utf-8")) + 2
        count += 1
    return used + pointer * (count + 2)


# -- what all connectors do the same way ------------------------------------

#: Any dotted release number. Target CLIs surround theirs with wording of their
#: own, so only the number is read out of it.
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
    argv: Sequence[str],
    cwd: str,
    deadline: Deadline,
    env: Optional[Iterable[Tuple[str, str]]] = None,
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
        env=tuple(env) if env is not None else environment(),
        stdin_text="",
        deadline=deadline,
    )


def qualified_version(
    printed: str,
    qualification: Qualification,
    warnings: List[str],
) -> str:
    """Read the installed version and warn when it is outside exercised evidence.

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
        warnings.append(
            "{0} reported version {1}; this connector was exercised against "
            "{2}. The required one-shot and restriction mechanics are present, "
            "but this version is outside the release evidence.".format(
                qualification.cli_identity,
                version,
                ", ".join(qualification.versions),
            )
        )
    return version


def qualified_platform(
    qualification: Qualification, warnings: List[str]
) -> str:
    """Describe this computer and warn when it is outside exercised evidence.

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
    untested = (
        family != qualification.os_family
        or major not in qualification.os_major_versions
        or (
            bool(qualification.architectures)
            and machine not in qualification.architectures
        )
    )
    if untested:
        qualified_platforms = "{0} major {1}".format(
            qualification.os_family,
            "/".join(qualification.os_major_versions),
        )
        if qualification.architectures:
            qualified_platforms += " on {0}".format(
                "/".join(qualification.architectures)
            )
        warnings.append(
            "{0} is running on {1}; this connector was exercised on {2}. "
            "The required one-shot and restriction mechanics are present, but "
            "this platform is outside the release evidence.".format(
                qualification.cli_identity, described, qualified_platforms
            )
        )
    return described


def qualified_restrictions(
    help_call: CompletedCall, qualification: Qualification
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
    if help_call.returncode != 0:
        raise BridgeError(
            Failure.RESTRICTIONS_UNAVAILABLE,
            detail="{0}'s help command exited {1}: {2}".format(
                qualification.cli_identity,
                help_call.returncode,
                (help_call.stderr or help_call.stdout).strip()[:160],
            ),
        )
    help_text = "{0}\n{1}".format(help_call.stdout, help_call.stderr)
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
    warnings: Sequence[str] = (),
    authentication_confirmed: bool = True,
) -> CheckResult:
    """The truthful mechanics and authentication state printed by ``check``.

    Written here rather than in each connector so that readiness reads the same
    way whichever harness was asked: where the program is, which version it is,
    what this computer is, and whether live authentication was actually
    established. Some CLIs expose no safe state-free authentication check; for
    those, successful readiness means the fixed call mechanics are present and
    the selected bounded call will report an authentication failure honestly.
    """
    if authentication_confirmed:
        state = "{0} is ready".format(harness_id)
    else:
        state = (
            "{0} mechanics are ready, but live authentication is unconfirmed"
        ).format(harness_id)
    return CheckResult(
        message=(
            "{0}: version {1} at {2}, on {3}, {4}, and every "
            "required fixed-vector switch is present.".format(
                state, version, program, described_platform, authentication
            )
        ),
        warnings=tuple(warnings),
    )


# -- the six-way switch ----------------------------------------------------


def is_courier_only(harness_id: str) -> bool:
    """Answer from literals only, without importing or inspecting a connector."""
    return (
        harness_id == "hermes"
        or harness_id == "minimax"
        or harness_id == "qwen"
    )


def _switch(harness_id: str) -> Optional[Any]:
    """Resolve one identifier to its connector, or raise for an unknown name.

    The branches are written out one by one on purpose: this is the whole list
    of programs Agent Bridge is willing to start, and every one of the six
    resolves to a module.

    They are imported inside this function for one ordinary reason: each of
    them uses the shapes and the helpers defined above, and a module cannot be
    half-imported into itself. The names are literal and there are six of them;
    nothing is searched for, and nothing is built from a string. Matching the
    branch before its import is what leaves all five unselected connectors
    wholly inert.
    """
    if harness_id == "codex":
        from . import codex

        return codex
    if harness_id == "claude":
        from . import claude

        return claude
    if harness_id == "zcode":
        from . import zcode

        return zcode
    if harness_id == "hermes":
        from . import hermes

        return hermes
    if harness_id == "minimax":
        from . import minimax

        return minimax
    if harness_id == "qwen":
        from . import qwen

        return qwen
    raise BridgeError(Failure.UNKNOWN_HARNESS, detail=harness_id)


def resolve(harness_id: str) -> Any:
    """Return the connector for one of the six literal targets.

    Raises UNKNOWN_HARNESS for any other name. Every fixed target ships a
    connector, so CONNECTOR_UNAVAILABLE is reserved for an incomplete build.
    """
    connector = _switch(harness_id)
    if connector is None:
        raise BridgeError(Failure.CONNECTOR_UNAVAILABLE, detail=harness_id)
    return connector

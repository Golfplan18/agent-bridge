"""The five harnesses Agent Bridge can call, and how one call is described.

Agent Bridge knows exactly five coding-agent harnesses, named once here and
never discovered at runtime. Looking one up is a literal five-way switch: there
is no registry, no plugin search, no dynamic import, and no base class for a
connector to inherit. Adding a sixth harness means editing this file, which is
the point - the set of programs the bridge will start is visible in source.

No connector ships in this build yet, so every branch of the switch resolves to
nothing and asking for one is an honest failure rather than a silent fallback.

This module also holds the two small fixed shapes the rest of the code passes
around: what a connector claims to have been tested against, and what one
bounded call to a peer program consists of.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional, Tuple

from .errors import BridgeError, Failure

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

    The last two fields are the connector saying, in plain terms, what it has
    granted this peer read access to with the restriction switches it put in
    `argv`: the project root, and the one review-evidence file. Neither has a
    default, because a connector that did not think about them must not be
    able to look as though it had. `None` is a real answer and means "none of
    this kind" - a turn with no project, or a turn that is not a review. The
    runner compares both against what it actually made, and refuses to start
    the peer if they disagree, so a reviewer cannot end up reading something
    other than the difference the runner wrote.
    """

    argv: Tuple[str, ...]
    cwd: str
    env: Tuple[Tuple[str, str], ...]
    project_root: Optional[str]
    review_evidence: Optional[str]


def _switch(harness_id: str) -> Optional[Any]:
    """Resolve one identifier to its connector, or raise for an unknown name.

    Every branch returns None in this build because no connector has shipped
    yet. The branches are written out one by one on purpose: this is the whole
    list of programs Agent Bridge is willing to start.
    """
    if harness_id == "codex":
        return None
    if harness_id == "claude":
        return None
    if harness_id == "zcode":
        return None
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

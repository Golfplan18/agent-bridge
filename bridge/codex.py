"""Calling Codex with its strongest practical read-only posture.

Codex is OpenAI's own command-line program for its coding agent. This module is
the whole of what Agent Bridge knows about it: which program to start, which
switches must be on it, and how to tell - without spending a model turn -
whether starting it would work at all.

There are two operations here and nothing else. `check` answers whether Codex
could be used right now. `build_command` composes the one fixed argument vector
a turn runs. Both do the same inexpensive prerequisites first, because a turn
that skipped them would find out about a missing sign-in or a renamed switch in
the middle of real work, with the peer already running.

**How the answer comes back.** `codex exec` puts its banner, the prompt it was
handed, its warnings and its errors on the error stream, and puts only the final
agent message on standard output. That separation is what lets the runner
publish what it captured as the peer's reply, word for word, without editing
anything out of it. The prompt travels the other way, on standard input, and the
lone `-` says so out loud: with no prompt argument Codex would read standard
input anyway, and naming it is what makes the handover deliberate rather than
incidental.

**The switches, and why none of them is decoration.**

`--ignore-user-config` skips `$CODEX_HOME/config.toml`, including any model or
effort defaults stored only there. It does not suppress trusted-project
`.codex/config.toml` files and project hooks or rules, system configuration,
`managed_config.toml`, `requirements.toml`, cloud-delivered requirements,
macOS MDM preferences, or separately sourced user/global hooks and rules. The
fixed vector therefore overrides every compatible high-risk route it can name,
and the connector reports the surviving configuration boundary rather than
claiming that the flag removes it. Signing in survives because authentication
is read from `CODEX_HOME` rather than from that file.

`--sandbox read-only` is Codex's own enforced sandbox, not a request to be
careful. A shell may still exist inside the turn; what it cannot do is write.
`--skip-git-repo-check` is there because the neutral directory a turn without a
project runs in is not a Git repository, and Codex otherwise refuses to start
outside one. `--cd` names the working root, and it is given the very directory
the process is started in, so the two cannot drift apart.

Four switches are deliberately never passed, and the reason is the same each
time: every one of them hands back something the switches above have just taken
away. Three are self-evident - `--ephemeral`,
`--dangerously-bypass-approvals-and-sandbox` and
`--dangerously-bypass-hook-trust`. The fourth, `--ignore-rules`, is the subtle
one: it drops the user's *own* execution-policy rules, so despite the sound of
it, it is a loosening.

**What readiness costs.** Nothing. Four cheap questions, no model turn among
them: where the program is, `codex --version`, `codex login status`, and
`codex exec --help`. The sign-in answer is read from the exit status rather
than from the words, because Codex prints `Logged in using ChatGPT` on the error
stream along with everything else it has to say about itself.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

from typing import List, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

#: The identifier this connector answers to, out of the six.
HARNESS_ID = "codex"

#: What this connector has actually been tested against, declared in source and
#: never inferred from the machine it is running on. `restrictions` names the
#: exact switches the vector below passes to hold the boundary: skip the main
#: user config, override compatible high-risk routes, use the enforced read-only
#: sandbox, work outside a Git repository, and name the working root.
QUALIFICATION = connectors.Qualification(
    cli_identity="codex",
    versions=("0.147.0",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--ignore-user-config",
        "-c",
        "--disable",
        "--sandbox",
        "--skip-git-repo-check",
        "--cd",
    ),
)

WARNING = (
    "--ignore-user-config skips only $CODEX_HOME/config.toml, including any "
    "model and effort defaults stored there; Agent Bridge supplies no "
    "replacement. Trusted-project .codex/config.toml files and project hooks "
    "or rules, system configuration, managed_config.toml, requirements.toml, "
    "cloud-delivered requirements, macOS MDM preferences, and separately "
    "sourced user/global hooks or rules can still apply. Those layers can add "
    "settings the fixed vector does not override, while managed defaults or "
    "MDM can override CLI options. The fixed scalar controls disable known "
    "web search, notify, "
    "hooks, apps, plugins, Codex Apps MCP, and agent routes, but named MCP "
    "servers, plugins, hooks, network, telemetry, or other integrations from "
    "surviving configuration may still retain external-effect routes outside "
    "the read-only shell sandbox."
)


def _prerequisites(
    deadline: Deadline, cwd: str
) -> Tuple[str, str, str, Tuple[str, ...]]:
    """Everything that has to be true before starting Codex is worth doing.

    Five questions in order, each one cheap and none of them a model turn: is
    the program here, is its version one this connector was tested against, is
    this computer one it was tested on, is somebody signed in, and does the
    installed version still have every switch the turn relies on. Any of them
    failing raises, so nothing further happens.

    Returns the three facts a readiness report needs and a turn uses: where the
    program is, which version answered, and how this computer describes itself.
    """
    warnings = []  # type: List[str]
    program = connectors.executable(QUALIFICATION.cli_identity)
    version = connectors.qualified_version(
        connectors.probe((program, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)

    signed_in = connectors.probe((program, "login", "status"), cwd, deadline)
    if signed_in.returncode != 0:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="codex login status exited {0}".format(
                signed_in.returncode
            ),
        )

    connectors.qualified_restrictions(
        connectors.probe((program, "exec", "--help"), cwd, deadline),
        QUALIFICATION,
    )
    warnings.append(WARNING)
    return program, version, described, tuple(warnings)


def check(deadline: Deadline, cwd: str) -> connectors.CheckResult:
    """Report whether Codex could be used right now, spending no model turn.

    `cwd` is a neutral directory made for this command, so the questions below
    are asked somewhere with nothing in it. No real project is touched, nothing
    is installed, nobody is logged in, no model or provider is chosen, and
    nothing is written down for next time.
    """
    program, version, described, warnings = _prerequisites(deadline, cwd)
    return connectors.readiness(
        HARNESS_ID, program, version, described, "signed in", warnings
    )


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    """The fixed argument vector for one turn, prerequisites confirmed first.

    The runner calls this inside the turn's own deadline, which is why the
    prerequisites are repeated here rather than trusted from an earlier
    readiness check: readiness may have been established days ago, or never.

    `cwd` is the directory the peer may read - the project named on the command
    line, or the neutral empty directory a turn without a project gets. It comes
    from the command line only. Nothing under a message's `## Body` heading is
    read anywhere in Agent Bridge, so no text a peer or a plan wrote can name a
    directory here.
    """
    program, _version, _described, warnings = _prerequisites(deadline, cwd)
    return connectors.PeerCommand(
        argv=(
            program,
            "exec",
            "--ignore-user-config",
            "-c",
            "web_search=disabled",
            "-c",
            "notify=[]",
            "--disable",
            "hooks",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "-c",
            "orchestrator.mcp.enabled=false",
            "-c",
            "agents.enabled=false",
            "--disable",
            "multi_agent_v2",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            cwd,
            "-",
        ),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
    )

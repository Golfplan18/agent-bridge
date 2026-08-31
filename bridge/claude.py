"""Calling Claude Code, and making that one call unable to change anything.

Claude Code is Anthropic's own command-line program for its coding agent. This
module is the whole of what Agent Bridge knows about it: which program to start,
which switches must be on it, and how to tell - without spending a model turn -
whether starting it would work at all.

There are two operations here and nothing else. `check` answers whether Claude
Code could be used right now. `build_command` composes the one fixed argument
vector a turn runs. Both do the same inexpensive prerequisites first, because a
turn that skipped them would find out about a missing sign-in or a renamed
switch in the middle of real work, with the peer already running.

**How the answer comes back.** `--print` with no prompt argument reads the
prompt from standard input, and `--output-format text` puts the final answer,
and only the final answer, on standard output; everything the program has to say
about itself goes to the error stream. That separation is what lets the runner
publish what it captured as the peer's reply, word for word.

**The switches, and why none of them is decoration.**

`--restricted` is the one that carries most of the weight. It takes away the
built-in tools that run commands or code, and WebFetch with them, unless
`--tools` names them back - which this connector never does. It ignores the
user's, the project's and the local settings files, so a turn is not shaped by
whatever happens to be configured on the machine. It confines the file tools to
the working directory the process was started in. And it refuses the
permission mode that would bypass permission checks altogether.

`--strict-mcp-config` says to use only the MCP servers named by `--mcp-config`.
No `--mcp-config` is passed, so that set is empty: the turn reaches no MCP
server at all, whatever is configured elsewhere.

`--tools Read,Glob,Grep` says which of the built-in tools survive. Reading files,
finding them by name, and searching inside them are all a peer needs to inspect a
repository and answer about it. Everything that edits, runs, fetches, publishes
or messages is simply not there.

`--permission-mode plan` is the harness's own enforced read-only posture, put on
top of the three above rather than instead of them. Defaults are never trusted
here: every one of these is passed on every call, even though some of them
overlap.

Five switches are deliberately never passed. `--dangerously-skip-permissions`
and `--allow-dangerously-skip-permissions` undo the boundary outright.
`--continue` and `--resume` would carry a previous conversation into this turn,
and every call Agent Bridge makes is a fresh one - the session record on disk is
the memory, not the harness's own history. `--bare` is the surprising one: it
sounds like less, but it abandons subscription sign-in and insists on an API
key instead, which is the opposite of leaving the harness's own authentication
where it already lives.

**There is no working-directory switch, and none is needed.** Claude Code works
in the directory its process was started in, so that directory is the mechanism,
and `--restricted` is what confines the file tools to it.

**What readiness costs.** Nothing. Four cheap questions, no model turn among
them: where the program is, `claude --version`, `claude auth status --json`, and
`claude --help`. The sign-in answer is JSON, so it is read rather than guessed
at: being signed in is required, and how - by subscription rather than by an API
key - is reported as an observed fact and gates nothing, because choosing
providers is not this project's business.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import json
from typing import Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import CompletedCall, Deadline

#: The identifier this connector answers to, out of the five.
HARNESS_ID = "claude"

#: What this connector has actually been tested against, declared in source and
#: never inferred from the machine it is running on. `restrictions` names the
#: exact switches the vector below passes to hold the boundary: restricted mode,
#: no MCP servers, a named set of read-only tools, and the harness's own
#: enforced planning posture.
QUALIFICATION = connectors.Qualification(
    cli_identity="claude",
    versions=("2.1.251",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--restricted",
        "--strict-mcp-config",
        "--tools",
        "--permission-mode",
    ),
)

#: The built-in tools a peer keeps: read a file, find files by name, search
#: inside them. Passed as one comma-separated value so the option cannot go on
#: swallowing the switches that follow it.
READ_ONLY_TOOLS = "Read,Glob,Grep"


def _signed_in(status: CompletedCall) -> str:
    """Read Claude Code's own answer about who is signed in, and say it plainly.

    Being signed in is the requirement, and it is taken from the harness's own
    JSON rather than inferred from anything. How the sign-in was made is
    reported alongside it - the method, the provider, and the subscription when
    there is one - because that is what tells a reader the harness is on its
    subscription rather than on an API key. None of it decides anything: this
    project chooses no provider, no model and no plan.

    An answer that cannot be read at all is treated as not being signed in. It
    is not literally the same thing, but the one useful next action is: sign in
    with the harness's own command and look again.
    """
    if status.returncode != 0:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="claude auth status exited {0}".format(status.returncode),
        )
    try:
        answer = json.loads(status.stdout)
    except ValueError as exc:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="claude auth status printed no readable JSON: {0}".format(
                exc
            ),
        )
    if not isinstance(answer, dict) or answer.get("loggedIn") is not True:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="claude auth status does not report being logged in",
        )
    described = "signed in through {0} on {1}".format(
        answer.get("authMethod") or "an unnamed method",
        answer.get("apiProvider") or "an unnamed provider",
    )
    subscription = answer.get("subscriptionType")
    if subscription:
        described += " with a {0} subscription".format(subscription)
    return described


def _prerequisites(deadline: Deadline, cwd: str) -> Tuple[str, str, str, str]:
    """Everything that has to be true before starting Claude Code is worth doing.

    Five questions in order, each one cheap and none of them a model turn: is
    the program here, is its version one this connector was tested against, is
    this computer one it was tested on, is somebody signed in, and does the
    installed version still have every switch the turn relies on. Any of them
    failing raises, so nothing further happens.

    Returns the four facts a readiness report needs and a turn uses: where the
    program is, which version answered, how this computer describes itself, and
    how the sign-in was made.
    """
    program = connectors.executable(QUALIFICATION.cli_identity)
    version = connectors.qualified_version(
        connectors.probe((program, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
    )
    described = connectors.qualified_platform(QUALIFICATION)

    account = _signed_in(
        connectors.probe(
            (program, "auth", "status", "--json"), cwd, deadline
        )
    )

    connectors.qualified_restrictions(
        connectors.probe((program, "--help"), cwd, deadline).stdout,
        QUALIFICATION,
    )
    return program, version, described, account


def check(deadline: Deadline, cwd: str) -> str:
    """Report whether Claude Code could be used right now, spending no turn.

    `cwd` is a neutral directory made for this command, so the questions below
    are asked somewhere with nothing in it. No real project is touched, nothing
    is installed, nobody is logged in, no model or provider is chosen, and
    nothing is written down for next time.
    """
    program, version, described, account = _prerequisites(deadline, cwd)
    return connectors.readiness(
        HARNESS_ID, program, version, described, account
    )


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    """The fixed argument vector for one turn, prerequisites confirmed first.

    The runner calls this inside the turn's own deadline, which is why the
    prerequisites are repeated here rather than trusted from an earlier
    readiness check: readiness may have been established days ago, or never.

    `cwd` is the directory the peer may read - the project named on the command
    line, or the neutral empty directory a turn without a project gets. It is
    both where the program is started and, because of `--restricted`, the limit
    of what its file tools can reach. It comes from the command line only.
    Nothing under a message's `## Body` heading is read anywhere in Agent
    Bridge, so no text a peer or a plan wrote can name a directory here.
    """
    program, _version, _described, _account = _prerequisites(deadline, cwd)
    return connectors.PeerCommand(
        argv=(
            program,
            "--print",
            "--restricted",
            "--strict-mcp-config",
            "--tools",
            READ_ONLY_TOOLS,
            "--permission-mode",
            "plan",
            "--output-format",
            "text",
        ),
        cwd=cwd,
        env=connectors.environment(),
    )

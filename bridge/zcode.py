"""Calling ZCode with its strongest practical read-only posture.

ZCode is Z.AI's coding agent. Its command-line program is not installed on
`PATH`: it is a JavaScript bundle shipped inside the desktop application, run by
a Node runtime, so a call is `node /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs ...`.
This module is the whole of what Agent Bridge knows about it: where those two
pieces are, which switches hold the boundary, how the message gets in, how the
answer comes back, and how to tell - without spending a model turn - whether
starting it would work at all.

There are two operations here and nothing else. `check` answers whether ZCode
could be used right now. `build_command` composes the one fixed argument vector
a turn runs. Both do the same inexpensive prerequisites first, because a turn
that skipped them would find out about a missing runtime, a renamed switch or a
newly enabled plugin in the middle of real work, with the peer already running.

**How the message gets in, and why this connector is the declared exception.**
Agent Bridge sends the outgoing body on standard input wherever a harness has
one. ZCode 0.16.5 has none for a one-shot prompt, and that was established by
probe, not by reading documentation: `--prompt` with no value is a parse error
before anything is read; `--prompt -` sends a literal dash, as the model's own
reply confirmed; `--attach /dev/stdin` hands the model a path that its file tool
refuses as a device file; and the complete headless option table, read out of
the installed bundle, names nothing else for a prompt. The plan therefore lets
this connector take the body as the final command-line argument, under three
conditions the runner enforces before anything is started or published: no byte
of the body may be able to begin a new argument, a body over 524288 bytes is
refused rather than truncated or spilled to a file, and the vector is passed to
the program directly with no shell.

The first condition is met by binding the body to the prompt option as one
argument, `--prompt=<body>`, and not by a bare `--`. Node's own argument parser,
run with ZCode's exact option table, settles that: a bare `--` makes whatever
follows it a positional, which ZCode reads as its command name and rejects with
"Unknown command"; `--prompt` followed by a body that begins with a hyphen is
refused as "argument is ambiguous"; `--prompt=<body>` comes back byte for byte,
with a body that began `---` and contained `--mode yolo`, `-p`, an `=` sign and
a line beginning `--disallowed-tools=`, the one pattern ZCode's own pre-parser
strips. Binding asks nothing of the parser: there is no new argument for a byte
of the body to begin. The runner adds the other two refusals, a NUL byte, which
no argument can carry, and an argument list that with the inherited environment
would not fit the operating system's limit.

One consequence is stated in the user documentation and repeated here: a
command line is visible to other processes under the same account and may be
captured by crash reporters and vendor telemetry, so a body carried this way can
reach logs outside the user's control. Standard input has no such exposure, and
is used everywhere it exists.

**How the answer comes back.** `--output-format text` puts the final answer,
and only the final answer, on standard output; on success the error stream is
empty, and on failure the program writes `Error: ...` there and exits nonzero.
ZCode prepends a reminder of its own about plan mode to what the model sees, so
the peer reads the body after a short block of the harness's text; the body
itself arrives whole.

**The switches, and what they really are.**

`--disallowed-tools <names>` is hard-enforced: each name is removed from the
tool set when the session is built, so a removed tool does not exist for the
model to call, and the same list is handed to every subagent the model starts.
Two facts about it, from the installed program, matter to anyone extending
this. An entry is reduced to the name before any opening parenthesis, so
`Bash(git *)` removes the whole Bash tool, not a pattern of commands. And a tool
from an MCP server is removed only by its exact name, `mcp__<server>__<tool>`,
known only once that server has started. The list this connector passes removes
every built-in tool that writes, runs, reaches out, delegates, schedules, asks
a person, or reads other sessions; what remains is Read, Glob, Grep and the
session's own to-do list.

`--mode plan` is ZCode's own enforced read-only posture, put on top of the
removed tools rather than instead of them. Its rules, read from the program,
allow a tool that is read-only and not destructive, deny everything else that
reaches them, and allow any MCP tool whose server does not mark it destructive.
That last rule is why the plugin check below exists.

`--cwd <path>` names the working directory, and it is given the very directory
the process is started in, so the two cannot drift apart. It comes from the
command line only; nothing under a message's `## Body` heading is read anywhere
in Agent Bridge.

Four switches that `--help` advertises do not exist on 0.16.5: `--settings`,
`--max-turns`, `--allowed-tools` and `--permission-mode` are each rejected as
an unknown option, on a subcommand and in prompt mode alike, and the bundle's
strict parser has no entry for them. On this build `--help` is not a reliable
description of the program. So readiness proves the switches it relies on by
passing them to a subcommand that spends no turn, not only by reading the help.

**Persistent configuration, reported before every call.** No switch on this
build sheds the user's enabled plugins or MCP servers for one call, and plan
mode admits non-destructive MCP tools; a probe on this machine showed a peer
under plan mode and the full deny list still holding nineteen iOS-simulator
tools from an enabled plugin. Both operations therefore read ZCode's own
`plugins list --json`, which spends no turn, and report any enabled plugin that
declares or resolves an MCP server or carries a hook. Unreadable inventory is
also reported rather than treated as proof that none exists. Skills and slash
commands do not become model-callable routes here: the Skill tool is in the
deny list, and a slash command is not something the model can call. What this
check cannot see is an MCP server configured directly in the user's
configuration file rather than by a plugin: ZCode lists those only inside a
session, at the cost of a turn, and the file itself holds the sign-in key and
is never opened here. The warning states that uncertainty plainly.

**Authentication, and what can honestly be said about it.** ZCode has no command
that reports sign-in without a model turn. `zcode login` is not a status check:
it unconditionally starts a fresh Z.AI OAuth flow, opens a browser, waits for
the callback, writes the OAuth tokens to a shared credential store, then
exchanges the access token over the network for a coding-plan key and rewrites
the program's own configuration file with that key, replacing whatever key was
there. What the program itself treats as "signed in" is that configuration file
holding a coding-plan provider with a non-empty key; the OAuth store is not
consulted for that, and a headless turn authenticates with the configured key.
That was shown without opening anything: `ZCODE_DATA_BASE_DIR` is the one
variable that relocates the credential store, so it was pointed at an empty
directory and a headless turn was run; the turn succeeded, which it could not
have done had the store been what authenticates it.

That has a consequence worth stating plainly. A key placed in that file by hand
and a key minted by the sanctioned login are the same field, put to the same
use, and nothing in the program tells them apart. The sanctioned and
unsanctioned arrangements differ only in provenance, and provenance is
established by write history: a completed login writes the credential store and
then, a second or two later, the configuration file. That pair of timestamps is
the one thing readiness can observe without opening either file, and it is
reported as an observation about provenance, never as proof of a working
sign-in. It is also an observation about a moment: the configuration file is
rewritten by ordinary settings changes too, such as enabling or disabling a
plugin, and after one of those the pair no longer lines up, which says nothing
about where the key came from. Nothing here opens, prints, copies or compares a credential; only file
names and modification times are looked at. Readiness therefore reports what is
observable - which files are present, whether their last writes have the
login's order and spacing, and whether an API-key environment variable is set -
and says outright that sign-in itself is not confirmed.

**What readiness costs.** Nothing. Where the runtime is, whether the bundle is
at its documented place, `--version`, `--help`, `version` with the switches the
turn relies on, `plugins list --json`, and the modification times of two files.
No model turn among them.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

#: The identifier this connector answers to, out of the six.
HARNESS_ID = "zcode"

#: The runtime the bundle needs. Found on PATH like any other program; ZCode's
#: own diagnostics report the Node it was started with, and that is the one used.
RUNTIME = "node"

#: Where the desktop application keeps the command-line program. There is no
#: `zcode` on PATH to find; this is the documented location of the bundle.
SCRIPT = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"

#: What this connector has actually been tested against, declared in source and
#: never inferred from the machine it is running on. `restrictions` names the
#: switches the vector below passes to hold the boundary: remove the tools that
#: could write or reach out, run under the enforced planning posture, and take
#: the working root from the command line.
QUALIFICATION = connectors.Qualification(
    cli_identity="zcode",
    versions=("0.16.5",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--disallowed-tools",
        "--mode",
        "--cwd",
    ),
)

#: The prefix the body is bound to as the final argument. One argument, so no
#: byte of the body can begin another; see the module docstring for why a bare
#: `--` cannot do this job on ZCode's parser.
BODY_ARGUMENT = "--prompt="

#: How the answer is asked for: the final response alone, on standard output.
#: Not printed by `--help`, so it is proven by the no-turn probe rather than
#: looked for in the help text.
OUTPUT_FORMAT = ("--output-format", "text")

#: The built-in tools removed from the peer, as one comma-separated value so the
#: option cannot go on swallowing the switches that follow it. Everything that
#: writes, runs, reaches out, delegates, schedules, changes mode, asks a person
#: or reads other sessions. Read, Glob and Grep stay, because a peer that cannot
#: read the project cannot answer about it.
DENIED_TOOLS = ",".join(
    (
        "Write",
        "Edit",
        "ApplyPatch",
        "NotebookEdit",
        "Bash",
        "WebFetch",
        "WebSearch",
        "Agent",
        "Task",
        "TaskOutput",
        "TaskStop",
        "Skill",
        "Workflow",
        "SendMessage",
        "RespondToCoordinator",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "TodoWrite",
        "ReadSessionContext",
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "js",
        "js_reset",
        "js_add_node_module_dir",
        "mcp__node_repl__js",
        "mcp__node_repl__js_reset",
        "mcp__node_repl__js_add_node_module_dir",
    )
)

#: The program's own configuration file - the place its sign-in test looks.
CONFIG_FILE = os.path.join("~", ".zcode", "cli", "config.json")

#: The shared credential store the login writes, relative to a base directory
#: that is the home directory unless this variable names another.
CREDENTIALS_FILE = os.path.join(".zcode", "v2", "credentials.json")
DATA_BASE_DIR_VARIABLE = "ZCODE_DATA_BASE_DIR"

#: An environment variable the program would take an API key from.
API_KEY_VARIABLE = "ZCODE_API_KEY"

#: How far apart the login's two writes may be and still read as one login.
LOGIN_PAIR_SECONDS = 60.0


def _program() -> Tuple[str, str]:
    """Where the runtime and the bundle are, or `MISSING_CLI` naming which is not.

    The runtime is looked up on PATH the way every other harness program is.
    The bundle is looked for at its one documented place; nothing is searched
    for, and nothing is installed or put on PATH.
    """
    runtime = connectors.executable(RUNTIME)
    if not os.path.isfile(SCRIPT):
        raise BridgeError(
            Failure.MISSING_CLI,
            detail="the ZCode desktop application's command-line bundle is "
            "not at {0}".format(SCRIPT),
        )
    return runtime, SCRIPT


def _modified(path: str) -> Optional[float]:
    """When a file was last written, by metadata only; None if it is absent."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _sign_in_facts() -> str:
    """What can be observed about sign-in without a turn and without reading.

    ZCode's own sign-in test is a coding-plan key inside its configuration
    file, so a missing configuration file is not signed in by the program's own
    rule and is reported as `AUTHENTICATION_REQUIRED`. Beyond that, only
    presence and modification times are looked at: whether the shared credential
    store is there, whether the two files were last written in the order and
    spacing of the login routine, and whether an API-key variable is set. The
    sentence says outright that sign-in is not confirmed, because it is not.
    """
    config = os.path.expanduser(CONFIG_FILE)
    config_written = _modified(config)
    if config_written is None:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="ZCode's own sign-in test is a coding-plan key in {0}, "
            "which is absent".format(config),
        )
    base = os.environ.get(DATA_BASE_DIR_VARIABLE) or os.path.expanduser("~")
    credentials = os.path.join(base, CREDENTIALS_FILE)
    credentials_written = _modified(credentials)

    parts = ["its configuration file is present at {0}".format(config)]
    if credentials_written is None:
        parts.append(
            "the shared credential store at {0} is absent".format(credentials)
        )
    else:
        gap = config_written - credentials_written
        if 0.0 <= gap <= LOGIN_PAIR_SECONDS:
            parts.append(
                "the shared credential store at {0} is present and the two "
                "were last written in the order and spacing of ZCode's own "
                "login routine (the store first, the configuration file "
                "{1:.1f}s later)".format(credentials, gap)
            )
        else:
            parts.append(
                "the shared credential store at {0} is present but the two "
                "were not last written as one login writes them (the "
                "configuration file is also rewritten by ordinary settings "
                "changes, such as enabling or disabling a plugin)".format(
                    credentials
                )
            )
    if os.environ.get(API_KEY_VARIABLE):
        parts.append(
            "{0} is set in this environment, which ZCode would use as an API "
            "key rather than a login".format(API_KEY_VARIABLE)
        )
    parts.append(
        "sign-in itself is not confirmed, because ZCode offers no command "
        "that reports it without spending a model turn"
    )
    return "; ".join(parts)


def _names(value: object) -> List[str]:
    """The strings in a list the program printed, and nothing else."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _plugin_fact(
    runtime: str, script: str, deadline: Deadline, cwd: str
) -> str:
    """Describe exposed plugin routes without turning uncertainty into refusal."""
    try:
        listing = connectors.probe(
            (runtime, script, "plugins", "list", "--json"), cwd, deadline
        )
    except BridgeError as error:
        return (
            "the plugin inventory could not be inspected ({0}), so enabled "
            "plugin hooks and MCP servers are unknown"
        ).format(error.failure.value)
    parsed = None
    if listing.returncode == 0:
        try:
            parsed = json.loads(listing.stdout)
        except ValueError:
            parsed = None
    plugins = parsed.get("plugins") if isinstance(parsed, dict) else None
    if not isinstance(plugins, list):
        return (
            "zcode plugins list --json could not be read (exit {0}: {1}), "
            "so enabled plugin hooks and MCP servers are unknown"
        ).format(
                listing.returncode,
                (listing.stderr or listing.stdout).strip()[:160],
        )

    enabled = 0
    exposing = []
    for plugin in plugins:
        if not isinstance(plugin, dict) or plugin.get("enabled") is not True:
            continue
        enabled += 1
        servers = []  # type: List[str]
        for name in _names(plugin.get("declaredMcpServerNames")) + _names(
            plugin.get("mcpServerNames")
        ):
            if name not in servers:
                servers.append(name)
        hooks = plugin.get("hookDetails")
        hook_count = len(hooks) if isinstance(hooks, list) else 0
        if not servers and not hook_count:
            continue
        what = []
        if servers:
            what.append("MCP server {0}".format(", ".join(servers)))
        if hook_count:
            what.append("{0} hook(s)".format(hook_count))
        exposing.append(
            "{0} ({1})".format(
                plugin.get("id") or plugin.get("name") or "an unnamed plugin",
                "; ".join(what),
            )
        )
    if exposing:
        return (
            "enabled plugins expose routes ZCode has no per-call switch to "
            "shed: {0}"
        ).format("; ".join(exposing))
    return (
        "no enabled plugin declares an MCP server or a hook ({0} of {1} "
        "installed plugins enabled)".format(enabled, len(plugins))
    )


def _prerequisites(
    deadline: Deadline, cwd: str
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    """Everything that has to be true before starting ZCode is worth doing.

    Seven questions in order, each cheap and none a model turn: is the runtime
    here, is the bundle here, is its version one this connector was tested
    against, is this computer one it was tested on, what can be observed about
    sign-in, does any enabled plugin expose what no switch can remove, and are
    the switches the turn relies on really accepted - proven by passing them to
    a subcommand that spends no turn, because this program's help text lists
    switches its parser rejects. Missing software, minimum local sign-in state,
    or required mechanics raises; plugin exposure or uncertainty and the lack
    of live OAuth evidence are returned as warnings.

    Returns the four facts a readiness report needs and a turn uses: the
    program as it is started, which version answered, how this computer
    describes itself, and what was observed about sign-in and plugins.
    """
    warnings = []  # type: List[str]
    runtime, script = _program()
    version = connectors.qualified_version(
        connectors.probe((runtime, script, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)
    sign_in = _sign_in_facts()
    plugins = _plugin_fact(runtime, script, deadline, cwd)

    connectors.qualified_restrictions(
        connectors.probe((runtime, script, "--help"), cwd, deadline),
        QUALIFICATION,
    )
    accepted = connectors.probe(
        (
            runtime,
            script,
            "version",
            "--disallowed-tools",
            "Edit",
            "--mode",
            "plan",
            "--cwd",
            cwd,
        )
        + OUTPUT_FORMAT,
        cwd,
        deadline,
    )
    if accepted.returncode != 0 or version not in accepted.stdout:
        raise BridgeError(
            Failure.RESTRICTIONS_UNAVAILABLE,
            detail="zcode rejected {0} on a subcommand that spends no turn: "
            "{1}".format(
                ", ".join(QUALIFICATION.restrictions + OUTPUT_FORMAT[:1]),
                (accepted.stderr or accepted.stdout).strip()[:160],
            ),
        )
    warnings.append(
        "ZCode cannot shed enabled plugins or directly configured MCP "
        "servers per call, and plan mode may admit non-destructive MCP tools; "
        "{0}. {1}.".format(plugins, sign_in)
    )
    warnings.append(
        "ZCode receives the complete message in one --prompt argument, which "
        "may be visible to other processes under the same account or to logs."
    )
    return (
        "{0} {1}".format(runtime, script),
        version,
        described,
        "minimum local configuration needed to attempt a call is present",
        tuple(warnings),
    )


def check(deadline: Deadline, cwd: str) -> connectors.CheckResult:
    """Report whether ZCode could be used right now, spending no model turn.

    `cwd` is a neutral directory made for this command, so the questions are
    asked somewhere with nothing in it. No real project is touched, nothing is
    installed, nobody is logged in, no model or provider is chosen, and nothing
    is written down for next time.
    """
    program, version, described, account, warnings = _prerequisites(deadline, cwd)
    return connectors.readiness(
        HARNESS_ID,
        program,
        version,
        described,
        account,
        warnings,
        authentication_confirmed=False,
    )


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    """The fixed argument vector for one turn, prerequisites confirmed first.

    The runner calls this inside the turn's own deadline, which is why the
    prerequisites are repeated here rather than trusted from an earlier
    readiness check: readiness may have been established days ago, or never,
    and a plugin may have been enabled since.

    `cwd` is the directory the peer may read - the project named on the command
    line, or the neutral empty directory a turn without a project gets. It is
    both where the program is started and what `--cwd` names. The body is not
    here: the runner binds it to `BODY_ARGUMENT` as the final argument, after
    its own refusals, and sends nothing on standard input.
    """
    runtime, script = _program()
    _program_name, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd
    )
    return connectors.PeerCommand(
        argv=(
            runtime,
            script,
            "--mode",
            "plan",
            "--cwd",
            cwd,
            "--disallowed-tools",
            DENIED_TOOLS,
        )
        + OUTPUT_FORMAT,
        cwd=cwd,
        env=connectors.environment(),
        body_argument=BODY_ARGUMENT,
        warnings=warnings,
    )

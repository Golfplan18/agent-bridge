"""Calling ZCode, and why this build of Agent Bridge cannot yet make that call.

ZCode is Z.AI's coding agent. Its command-line program is not installed on
`PATH`: it is a JavaScript bundle shipped inside the desktop application, run by
a Node runtime, so a call is `node /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs ...`.
This module is the whole of what Agent Bridge knows about it: where those two
pieces are, which switches would hold the boundary, how to tell without spending
a model turn whether starting it would work at all - and the one fact that stops
a turn from being run.

**The finding that shapes this module.** Agent Bridge sends the outgoing message
to a peer on standard input and nowhere else; prompt text never appears on a
command line. ZCode 0.16.5 takes a prompt only on its command line, through
`--prompt <text>` or `-p <text>`. Every way of getting a body in through
standard input was tried on this build. `--prompt` with no value is a parse
error before anything is read. `--prompt -` sends a literal dash: the model's
own reply said it had received "just a `-`". `--attach /dev/stdin` hands the
model a path rather than reading it, and the model's file tool refused it as a
device file; the program's own file reader also refuses anything that is not a
regular file. An empty prompt is refused with "--prompt requires non-empty
text." The complete headless option set, read out of the installed bundle, names
nothing else for a prompt. The one entry point that does read standard input is
`zcode app-server`, the desktop application's own multi-step protocol, whose
client identifies itself as the desktop application; the plan excludes private
desktop endpoints and forbids substituting desktop internals when a public CLI
fails, so it is not an option here.

So there is no fixed argument vector this module can honestly compose. Both
operations below run every inexpensive prerequisite - so a missing runtime, a
missing bundle, an untested version, an untested platform, or a vanished switch
is reported as exactly that - and then refuse with `CONNECTOR_UNAVAILABLE`,
saying why in one sentence. A later ZCode that reads a prompt from standard
input needs only `build_command` written; everything else here stands.

**The switches that would hold the boundary, and what they really are.**

`--disallowed-tools <names>` is hard-enforced: each name is removed from the
tool set when the session is built, so a removed tool does not exist for the
model to call, and the same list is handed to every subagent the model starts.
Two things about it were established from the installed program and matter to
anyone extending this. An entry is reduced to the name before any opening
parenthesis, so `Bash(git *)` removes the whole Bash tool, not a pattern of
commands. And a tool from an MCP server is removed only by its exact name,
`mcp__<server>__<tool>`, which is known only once that server has been started.

`--mode plan` is ZCode's own enforced read-only posture, put on top of the
removed tools rather than instead of them. Its permission rules, read from the
installed program, allow a tool that is read-only and not destructive, deny
everything else that reaches them - and allow any MCP tool whose server does not
mark it destructive. `--disallowedTools` is checked before the mode is, and a
removed tool never reaches the rules at all.

`--cwd <path>` names the working directory, and it is given the very directory
the process is started in, so the two cannot drift apart. The directory comes
from the command line only.

Three switches that `--help` advertises do not exist on 0.16.5: `--settings`,
`--max-turns` and `--allowed-tools` are each rejected as an unknown option, on
a subcommand and in prompt mode alike, and the bundle's strict parser has no
entry for them. The consequence is that nothing can shed the user's enabled
plugins and MCP servers for one call, the way Codex's `--ignore-user-config`
does; whatever is enabled in the user's configuration is present in every turn.
Because the help text lies about switches, readiness proves the three switches
above by passing them to a subcommand that spends no turn, not only by reading
the help.

What the posture actually held, on this machine, in a disposable Git repository
with tracked and untracked canaries: under `--mode plan` and a deny list naming
every built-in tool that writes, runs, reaches out or delegates, the peer could
not create, change or delete a file, could not write into `.git`, could not run
a shell command and could not fetch a page - the harness's own event stream
shows only Glob and Read being called, because nothing else existed to call -
and every hash, ref and status line was unchanged afterwards with no lock left
behind. But the same peer still held nineteen MCP tools from an enabled
plugin, which plan mode permits. So on this build the non-mutation property is
holdable by switch and the no-external-effect property is not, unless every
MCP tool the user has enabled is named exactly in the deny list per call.

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
sign-in. Nothing here opens, prints, copies or compares a credential; only file
names, sizes and modification times are looked at. Readiness therefore reports
what is observable - which files are present, whether their last writes have
the login's order and spacing, and whether an API-key environment variable is
set - and says outright that sign-in itself is not confirmed. It never prints a
readiness sentence it cannot stand behind.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
from typing import NoReturn, Optional, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

#: The identifier this connector answers to, out of the five.
HARNESS_ID = "zcode"

#: The runtime the bundle needs. Found on PATH like any other program; ZCode's
#: own diagnostics report the Node it was started with, and that is the one used.
RUNTIME = "node"

#: Where the desktop application keeps the command-line program. There is no
#: `zcode` on PATH to find; this is the documented location of the bundle.
SCRIPT = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"

#: What this connector has actually been tested against, declared in source and
#: never inferred from the machine it is running on. `restrictions` names the
#: switches that would hold the boundary and are proven present on every check:
#: remove the tools that could write or reach out, run under the enforced
#: planning posture, and take the working root from the command line.
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

#: Why no turn can be run against this build, in one sentence.
CANNOT_CALL = (
    "build 0.16.5 accepts a prompt only on its command line - `--prompt -` "
    "sends a literal dash and `--attach /dev/stdin` is refused as a device "
    "file - and Agent Bridge sends the message on standard input only, so "
    "this build cannot call it"
)


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
                "were not last written as one login writes them".format(
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


def _prerequisites(deadline: Deadline, cwd: str) -> Tuple[str, str, str, str]:
    """Everything that would have to be true before starting ZCode.

    Six questions in order, each cheap and none a model turn: is the runtime
    here, is the bundle here, is its version one this connector was tested
    against, is this computer one it was tested on, what can be observed about
    sign-in, and are the three switches really accepted - proven by passing
    them to a subcommand that spends no turn, because this program's help text
    lists switches its parser rejects. Any of them failing raises, so nothing
    further happens.

    Returns the four facts a report needs: the program as it would be started,
    which version answered, how this computer describes itself, and what was
    observed about sign-in.
    """
    runtime, script = _program()
    version = connectors.qualified_version(
        connectors.probe((runtime, script, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
    )
    described = connectors.qualified_platform(QUALIFICATION)
    account = _sign_in_facts()

    connectors.qualified_restrictions(
        connectors.probe((runtime, script, "--help"), cwd, deadline).stdout,
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
        ),
        cwd,
        deadline,
    )
    if accepted.returncode != 0 or version not in accepted.stdout:
        raise BridgeError(
            Failure.RESTRICTIONS_UNAVAILABLE,
            detail="zcode rejected {0} on a subcommand that spends no turn: "
            "{1}".format(
                ", ".join(QUALIFICATION.restrictions),
                (accepted.stderr or accepted.stdout).strip()[:160],
            ),
        )
    return "{0} {1}".format(runtime, script), version, described, account


def _refusal(
    program: str, version: str, described: str, account: str
) -> NoReturn:
    """Refuse, with every verified fact in front of the reason."""
    raise BridgeError(
        Failure.CONNECTOR_UNAVAILABLE,
        detail="zcode {0} at {1}, on {2}; {3}; {4}".format(
            version, program, described, account, CANNOT_CALL
        ),
    )


def check(deadline: Deadline, cwd: str) -> str:
    """Report whether ZCode could be used right now, spending no model turn.

    `cwd` is a neutral directory made for this command, so the questions are
    asked somewhere with nothing in it. No real project is touched, nothing is
    installed, nobody is logged in, no model or provider is chosen, and nothing
    is written down for next time.

    On this build the answer is always no, and it is given with the verified
    facts first - runtime and bundle found, version, platform, the observable
    sign-in facts, the switches - so that the one sentence says exactly which
    thing stands in the way. A missing program, an untested version or a gone
    switch is reported as that instead, because it is the nearer problem.
    """
    program, version, described, account = _prerequisites(deadline, cwd)
    _refusal(program, version, described, account)


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    """No fixed argument vector exists for this build; refuse before a request.

    The runner calls this inside the turn's own deadline and before publishing
    the request, so refusing here leaves the session record untouched: nothing
    was sent, and nothing says otherwise. The prerequisites run first for the
    same reason they do in `check`.
    """
    program, version, described, account = _prerequisites(deadline, cwd)
    _refusal(program, version, described, account)

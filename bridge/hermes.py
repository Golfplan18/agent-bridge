"""Calling Hermes Agent as a courier, and making that call unable to change anything.

Hermes Agent is Nous Research's command-line coding agent. This module is the
whole of what Agent Bridge knows about it: which program to start, which
switches must be on it, how the message reaches it, and how to tell - without
spending a model turn - whether starting it would work at all.

There are two operations here and nothing else. `check` answers whether Hermes
could be used right now. `build_command` composes the one fixed argument vector
a turn runs. Both do the same inexpensive prerequisites first, because a turn
that skipped them would find out about a missing sign-in or a renamed switch in
the middle of real work, with the peer already running.

**A courier, not a reviewer.** Hermes switches tools on and off in whole
groups, and the group that reads a file is the group that writes one: `file`
holds read, write, patch and search together and has no read-only sibling.
Probed on 0.18.2: under `-t file` the peer read a canary file correctly, and
under `-t file` it then modified a tracked file and created a new one. A Hermes
peer given a project could therefore read it only by also being able to write
anywhere the account can, so this connector declares itself courier-only
instead: it is given no project, `run` refuses `--project` for it, and it
answers on exactly what the request contains. That is a smaller peer, not a
weaker boundary. A peer holding no file tool can neither read nor write
anything, and the runner writes both canonical messages itself, so the
exchange never needs the peer to touch a file.

**How the message reaches it.** Hermes has no standard-input path for a
one-shot prompt, and that was established by probe rather than assumed: `-z -`
handed the model a lone dash, which it answered as such, and an empty `-z` fell
into the interactive banner, echoed the piped text, and exited without
answering. So this is one of the connectors permitted to hand the body over as
the final command-line argument, and the three conditions on doing so are met.
The body can never be read as an option: Hermes's own parser refuses a bare
`--` in front of a single option value (`-z -- ---` and `-z -- --- x` both exit
2 before any turn), so the body is attached to the option instead, as
`--oneshot=<body>`, which the parser splits at the first `=` and accepts
whatever follows - probed with bodies beginning `---` and `-h`. A body that
would make the whole argument block too large for this computer, or that holds
a NUL byte, is refused by the runner before the request is published, never
truncated, split or spilled to a file. And the vector is passed directly to the
program with no shell, as everything the bridge starts is. The cost is the one
the plan states once: a command line is visible to other processes under the
same account and can reach logs the user does not control.

**The switches, and why none of them is decoration.**

`--toolsets todo` is the entire restriction surface, because `-z` bypasses
every approval of its own accord. It is enforced twice: a tool outside the
named toolsets is never shown to the model, and a call to one anyway is refused
at dispatch with Hermes's own "does not exist". Under it a battery asking for a
file append, a file creation, a deletion, a write into `.git/refs/heads`, a
shell command and a web fetch changed nothing in a throwaway repository, and
the run ended at Hermes's refusal of the shell call. Names are toolsets only,
never individual tools. `todo` is one harmless planner tool, and it is there
rather than nothing because with no tool at all the model invented a tool list
and invented file contents when asked; one real tool, and every other attempt
refused, is the smallest posture that keeps it honest.

`--safe-mode` sheds what a user's own configuration could otherwise bring into
a turn - plugin discovery, MCP servers and shell hooks, all of which Hermes's
startup skips when the switch is set - while leaving the configured provider
alone: probed, a `--safe-mode` turn answered through the Nous Portal exactly as
a bare one did. The two things it also asks for do not happen on the one-shot
path, and are stated rather than relied on: it implies `--ignore-user-config`,
which the loader this path uses does not read, and `--ignore-rules`, which is
parsed but unwired here, so Hermes's memory loads as normal and a project's
`AGENTS.md` would too. There is no project, so the second is moot; the first is
why the provider survives.

Hermes holds keys for other providers in its own environment as well as the
Portal sign-in, and when its configuration names no provider it ranks a Portal
login last, behind those keys. `hermes portal info` reports both facts that
matter, on one page and without a model turn: whether the Portal is signed in,
and whether the configuration selects it. Readiness requires both. This
connector chooses no provider and no model - `--provider` is never passed,
because Hermes then insists on `--model` too - and it declines to run through
anything but the subscription.

**One name is taken out of the environment.** `TERMINAL_CWD` is a variable a
Hermes session exports into every program it starts, and a child Hermes reads
it before its own working directory - for the files a tool would touch, and for
the `AGENTS.md` it loads at startup. Probed: started in one directory with the
variable naming another, it worked in the other. The working directory comes
from the command line only, so that one name is dropped from the environment
this connector hands the child. Nothing else is touched: the harness finds its
own sign-in and settings where it always does.

Each `-z` run is a fresh session; `--resume` and `--continue` are ignored
beside it and never passed. Nothing daemonizes - the process group was empty
after every probe - and there is no wall-clock limit of its own, so the turn's
deadline and cleanup are the only bound. Only the final answer goes to standard
output; everything else Hermes has to say goes to the error stream or nowhere.

**What readiness costs.** Nothing. Four cheap questions, no model turn among
them: where the program is, `hermes --version`, `hermes portal info`, and
`hermes --help`. The sign-in answer is read from the words, because `portal
info` exits 0 whether or not anyone is signed in and has no machine-readable
form; what is looked for is written down once below.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

from typing import Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import CompletedCall, Deadline

#: The identifier this connector answers to, out of the five.
HARNESS_ID = "hermes"

#: This peer is given no project and answers on what it is sent. The command
#: line reads this name and refuses `--project` for it before anything starts.
COURIER_ONLY = True

#: What this connector has actually been tested against, declared in source and
#: never inferred from the machine it is running on. `restrictions` names the
#: exact switches the vector below passes to hold the boundary: the one-shot
#: mode that keeps everything but the answer off standard output, the toolset
#: list that is the whole of the restriction surface, and the mode that sheds
#: plugins, MCP servers and shell hooks.
QUALIFICATION = connectors.Qualification(
    cli_identity="hermes",
    versions=("0.18.2",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--oneshot",
        "--toolsets",
        "--safe-mode",
    ),
)

#: The one toolset a peer keeps: a planner that touches nothing.
TOOLSET = "todo"

#: How the body is attached: as the one-shot option's own value, which Hermes's
#: parser splits at the first `=`, so a body beginning with a hyphen is still
#: the body. The runner appends this prefix and the body as one final argument.
BODY_ARGUMENT = "--oneshot="

#: The one environment name dropped before the child is started, because a
#: child Hermes reads it in place of its own working directory.
DROPPED_FROM_ENVIRONMENT = ("TERMINAL_CWD",)

#: What `hermes portal info` says when the Portal is signed in, and when the
#: configuration selects it as the inference provider. Both are required.
SIGNED_IN_LINE = "logged in"
NOT_SIGNED_IN_LINE = "not logged in"
PORTAL_SELECTED_LINE = "using Nous as inference provider"


def _environment() -> Tuple[Tuple[str, str], ...]:
    """This process's environment with the one redirecting name taken out."""
    return tuple(
        (name, value)
        for name, value in connectors.environment()
        if name not in DROPPED_FROM_ENVIRONMENT
    )


def _signed_in(status: CompletedCall) -> str:
    """Read Hermes's own answer about the Portal, and say it plainly.

    Two things have to be true and both are on the one page: somebody is signed
    in to the Nous Portal, and the configuration names it as the provider a
    turn would use. The second matters as much as the first, because Hermes
    keeps API keys for other providers alongside the sign-in and prefers them
    when the configuration is silent. A signed-in Hermes configured for another
    provider is reported as needing the harness's own login command, which is
    also the command that selects the Portal.
    """
    if status.returncode != 0:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="hermes portal info exited {0}".format(status.returncode),
        )
    text = status.stdout
    if NOT_SIGNED_IN_LINE in text or SIGNED_IN_LINE not in text:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="hermes portal info does not report being logged in to "
            "the Nous Portal",
        )
    if PORTAL_SELECTED_LINE not in text:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="hermes is signed in to the Nous Portal but its "
            "configuration selects another inference provider; Agent Bridge "
            "runs a peer only on its subscription",
        )
    return "signed in to the Nous Portal, which is the configured provider"


def _prerequisites(deadline: Deadline, cwd: str) -> Tuple[str, str, str, str]:
    """Everything that has to be true before starting Hermes is worth doing.

    Five questions in order, each one cheap and none of them a model turn: is
    the program here, is its version one this connector was tested against, is
    this computer one it was tested on, is the subscription signed in and
    selected, and does the installed version still have every switch the turn
    relies on. Any of them failing raises, so nothing further happens.

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
        connectors.probe((program, "portal", "info"), cwd, deadline)
    )

    connectors.qualified_restrictions(
        connectors.probe((program, "--help"), cwd, deadline).stdout,
        QUALIFICATION,
    )
    return program, version, described, account


def check(deadline: Deadline, cwd: str) -> str:
    """Report whether Hermes could be used right now, spending no model turn.

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

    `cwd` is always the neutral empty directory a turn without a project gets,
    because this connector is courier-only and the command line refuses
    `--project` for it. The body is not in the vector returned here: the runner
    appends it as the final argument, behind `--oneshot=`, once it has checked
    the body will fit on a command line at all.
    """
    program, _version, _described, _account = _prerequisites(deadline, cwd)
    return connectors.PeerCommand(
        argv=(
            program,
            "--safe-mode",
            "--toolsets",
            TOOLSET,
        ),
        cwd=cwd,
        env=_environment(),
        body_argument=BODY_ARGUMENT,
    )

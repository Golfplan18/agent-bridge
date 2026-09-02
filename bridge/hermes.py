"""Calling Hermes Agent: what was established, and why no turn can be started.

Hermes Agent is Nous Research's command-line coding agent. This module is the
whole of what Agent Bridge knows about it: which program to start, which
switches would have to be on it, how to tell - without spending a model turn -
whether it is installed, qualified and signed in, and the one fact that stops
every turn: the installed version has no way to take the message on standard
input.

There are two operations here and nothing else, the same two every connector
has. `check` answers whether Hermes could be used right now. `build_command`
would compose the one fixed argument vector a turn runs. On version 0.18.2
both end the same way, after the same inexpensive questions: with a refusal
that names the reason. That is deliberate. The readiness entry point of every
native package then reports Hermes truthfully - installed, signed in, and
still not callable - and `run` refuses before it has published a request it
could not send.

**Why no turn can be started.** Hermes's non-interactive mode is `-z PROMPT`
(`--oneshot`). Its output is the right shape - only the final answer goes to
standard output - but it reads the prompt from the command line and from
nowhere else. Nothing on that path reads standard input. Probed on 0.18.2:
`-z -` handed the model a lone dash, which it answered as such; an empty `-z`
fell into the interactive banner, echoed the piped text, and exited without
answering. `chat -q` takes its text the same way. The frozen interface says the
body reaches a peer on standard input and never on a command line, and that is
a safety property rather than a preference, so it is not something a connector
may trade for a working call. A temporary file named on the command line, or a
shell, would be exactly that trade. Until a Hermes release reads a one-shot
prompt from standard input there is no vector to compose, and this module does
not pretend to one.

**What was established for the day there is one.** All of it by probe, none
of it from documentation.

`-t TOOLSETS` (`--toolsets`) is the entire restriction surface, because `-z`
bypasses every approval prompt of its own accord. It is enforced twice: a tool
outside the named toolsets is never shown to the model, and a call to one
anyway is refused at dispatch. Under `-t todo`, a battery asking for a file
append, a file creation, a deletion, a write into `.git/refs/heads`, a shell
command and a web fetch changed nothing in a throwaway repository, and the run
ended at Hermes's own refusal of the shell call. Names are toolsets only, never
individual tools, and `file` is one toolset holding read, write, patch and
search together with no read-only sibling. Probed: `-t file` read a canary
file correctly, and `-t file` then modified a tracked file and created a new
one. So with a project in front of it Hermes can read and write, or neither.
Under the plan's non-mutation rule that leaves a Hermes peer able to answer a
question that carries its own context, and unable to read a repository: it
cannot review code. With no real tool at all (`-t context_engine` resolves to
zero tools) the model invented a tool list and invented file contents, so
`todo` - one harmless planner tool, and every real attempt refused - is the
smallest posture that keeps it honest.

Hermes holds keys for other providers in its own environment as well as the
Nous Portal sign-in, and when its configuration names no provider it ranks a
Portal login last, behind those keys. `hermes portal info` reports both facts
that matter, on the same page and without a model turn: whether the Portal is
signed in, and whether the configuration selects it. Readiness requires both.
This connector chooses no provider and no model; it declines to run through
anything but the subscription. `--ignore-user-config` and `--safe-mode` next to
`-z` both kept the configured provider when probed, because the one-shot path
loads configuration through a loader that ignores that flag; `--safe-mode`
also turns off plugin discovery, MCP servers and shell hooks, which are the
channels a user's configuration could otherwise open into a turn, so it would
be the switch to pass. `--ignore-rules` is parsed but unwired on the one-shot
path: a project's `AGENTS.md` loads verbatim, and so does memory. The plan
states that boundary rather than suppressing it.

`TERMINAL_CWD` is a variable a Hermes session exports into every program it
starts, and a child Hermes reads it before its own working directory. Probed:
started in one directory with the variable naming another, it worked in the
other. A working connector would have to drop that one name from the
environment it hands the child, because the working directory must come from
the command line only. Each `-z` run is a fresh session, `--resume` and
`--continue` are ignored beside it, nothing daemonizes - the process group was
empty after every probe - and there is no wall-clock limit of its own, so the
turn's deadline and cleanup are the only bound.

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

#: What this connector has actually been tested against, declared in source and
#: never inferred from the machine it is running on. `restrictions` names the
#: switches a turn would rely on: the one-shot mode that keeps everything but
#: the answer off standard output, and the toolset list that is the whole of
#: the restriction surface.
QUALIFICATION = connectors.Qualification(
    cli_identity="hermes",
    versions=("0.18.2",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--oneshot",
        "--toolsets",
    ),
)

#: What `hermes portal info` says when the Portal is signed in, and when the
#: configuration selects it as the inference provider. Both are required.
SIGNED_IN_LINE = "logged in"
NOT_SIGNED_IN_LINE = "not logged in"
PORTAL_SELECTED_LINE = "using Nous as inference provider"


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
    """Everything that would have to be true before starting Hermes.

    Five questions in order, each one cheap and none of them a model turn: is
    the program here, is its version one this connector was tested against, is
    this computer one it was tested on, is the subscription signed in and
    selected, and does the installed version still have every switch a turn
    would rely on. Any of them failing raises, so nothing further happens.

    Returns the four facts a readiness report needs: where the program is,
    which version answered, how this computer describes itself, and how the
    sign-in was made.
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


def _no_input_path(
    program: str, version: str, described: str, account: str
) -> BridgeError:
    """The refusal both operations end in, with everything else that is true.

    Every earlier question passed, and the person reading this should know
    that: the one thing missing is a way to hand the message over. This build
    ships no connector able to call Hermes, and says so with the reason.
    """
    return BridgeError(
        Failure.CONNECTOR_UNAVAILABLE,
        detail=(
            "hermes {0} at {1}, on {2}, {3}, offers every restriction switch "
            "this connector would pass, but its one-shot mode reads the "
            "prompt only from the command line and Agent Bridge sends the "
            "body only on standard input, so no turn can be started until a "
            "Hermes release reads a one-shot prompt from standard "
            "input".format(version, program, described, account)
        ),
    )


def check(deadline: Deadline, cwd: str) -> str:
    """Report whether Hermes could be used right now, spending no model turn.

    `cwd` is a neutral directory made for this command, so the questions below
    are asked somewhere with nothing in it. No real project is touched, nothing
    is installed, nobody is logged in, no model or provider is chosen, and
    nothing is written down for next time.

    On the qualified version the answer is no, and the refusal says why: the
    questions that passed are named in it, so a missing program or sign-in is
    reported as itself, and an installed, signed-in Hermes is reported as
    installed, signed in, and not callable.
    """
    program, version, described, account = _prerequisites(deadline, cwd)
    raise _no_input_path(program, version, described, account)


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    """Refuse to compose a turn, after the same prerequisites as readiness.

    The runner calls this inside the turn's own deadline and before it
    publishes the request, so a refusal here leaves the session exactly as it
    was: nothing was sent, and no message says otherwise. There is no vector
    that would carry the body on standard input, so none is returned.
    """
    program, version, described, account = _prerequisites(deadline, cwd)
    raise _no_input_path(program, version, described, account)

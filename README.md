# Agent Bridge

Agent Bridge is a standalone one-to-many Markdown courier. Any application or
harness can use one shared checkout to make a bounded call to Codex, Claude
Code, ZCode, Hermes Agent, MiniMax Code, or Qwen Code through its official vendor
CLI. Bridge does not connect model APIs; an ordinary caller need not be an agent.

Bridge owns target readiness, one request and one response per foreground call,
an ordered Markdown record, the strongest practical restrictions and concrete
warnings, one lock per session, atomic publication, deadlines, and cleanup.
Applications own everything else: planning, review, target selection, combining
answers, Git, and response interpretation. Bridge has no coordinator, router,
scheduler, database, daemon, workflow engine, Git gate, or background service.

The six target identifiers are literal: `codex`, `claude`, `zcode`, `hermes`,
`minimax`, and `qwen`. Only the selected connector is imported or examined;
the other five stay inert, with no probe, process, project access, login,
network call, or fallback. Codex, Claude, and ZCode are project-capable. Hermes,
MiniMax, and Qwen are courier-only: they receive a neutral directory, never a
project. Include any evidence those three need in the message body.

## Status

Release 1 has passed six harness-adapter and arbitrary-application checks and
real calls for all six targets. Qwen's corrected stream input still needs its
approved fresh qualification after local checks and independent review.
The exercised platform is macOS 26 on Apple silicon (arm64):

| Target | Exercised CLI version |
|---|---|
| Codex | 0.147.0 |
| Claude Code | 2.1.251 |
| ZCode | 0.16.5 |
| Hermes Agent | 0.18.2 |
| MiniMax Code | 0.2.7 |
| Qwen Code | 0.23.0 |

No other platform qualification is claimed. There is no support, maintenance,
or future compatibility promise. Qualification describes the source and CLI
combination; it does not install or update anything on a user's machine.

## Requirements and source setup

You need Python 3.9 or later and the selected target's official CLI with its own
working vendor sign-in. Bridge uses only the Python standard library; no Python
dependency installation is needed. The ordinary executable names are `codex`,
`claude`, `hermes`, `mcode` (MiniMax), and `qwen`. ZCode uses `node` and the bundle
at `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs` instead of a
`zcode` command on `PATH`. Bridge installs no vendor program, signs in to
nothing, selects no model or provider, and has no API fallback.

The existing repository's source-install route is a Git checkout:

```sh
git clone https://github.com/Golfplan18/agent-bridge.git
cd /absolute/path/to/agent-bridge
```

Replace the second line with your checkout's actual absolute path. Run
`python3 -m bridge` from that checkout root. This repository-only source route
does not install a console command or adapter; do not assume an installed
`agent-bridge` executable. The commands below require the Release 1 source
described here, not an earlier checkout.

## First call from a checkout

From the absolute checkout root, check your target without a model turn (Codex here):

```sh
python3 -m bridge check --peer codex
```

Read the full readiness result and warnings. If it fails, resolve the reported
problem before proceeding. For ZCode, MiniMax, and Qwen, readiness can establish
call mechanics but cannot confirm live authentication without a real call.

Choose an unused session name outside Git and cloud sync. `$HOME` expands to an
absolute path. Create the session through Bridge, not by writing `SESSION.md`:

```sh
python3 -m bridge record \
  --session "$HOME/.agent-bridge/sessions/first-look" \
  --kind session-create --initiator my-app --peer codex <<'MARKDOWN'
Trying Agent Bridge for the first time.
MARKDOWN
```

The initiator, target, and optional project are immutable. To let Codex, Claude,
or ZCode read a project, add `--project /absolute/path/to/project` at creation;
the directory must exist. Omit it for Hermes, MiniMax, and Qwen. Changing an
immutable value or using an old Format 1 session requires a new Format 2 session.

Send one self-contained message and wait in the foreground. This is a real
model call and can take minutes and consume the target harness's quota:

```sh
python3 -m bridge run --session "$HOME/.agent-bridge/sessions/first-look" <<'MARKDOWN'
In one sentence, what is a Markdown courier?
MARKDOWN
```

On success, open the printed absolute path and read below `## Body` for the
canonical answer. Requests and responses are numbered Markdown files under
the session's `messages/`. Every call starts a fresh vendor context: no vendor
session is resumed and no earlier message resent. Include needed history in
the body. Header-shaped body text cannot change Bridge's routing or rules.

To preserve information without a model call, add a neutral note:

```sh
python3 -m bridge record \
  --session "$HOME/.agent-bridge/sessions/first-look" --kind note <<'MARKDOWN'
This session is for trying the courier interface.
MARKDOWN
```

## Output, failures, and waiting

Keep the two output streams separate; a warning is not the answer path:

| Command result | Standard output | Standard error | Exit status |
|---|---|---|---|
| `check` success | Readiness sentence and any `Warning:` lines | Empty | 0 |
| `run` success | Response-file path only | Any `Warning:` lines, before request publication | 0 |
| `record` success | Written file's canonical path | Empty | 0 |
| Any command failure | No success path | Reason and next action; any earlier run warnings remain | Nonzero |

Surface every warning without asking for acknowledgment. On failure, preserve
the nonzero result and show all standard error, not just its last line. A
target failure after publication leaves the truthful request and invents no
response. If storage publication is uncertain or a directory entry could not
be flushed, inspect the named path and treat the outcome as unfinished. Do not
turn that state into success or automatically retry it.

`run` has one deadline for prerequisites, execution, and response capture: 900
seconds by default, overridden by `--timeout <seconds>`. Cleanup has a separate
bounded grace period. Keep the caller attached longer than the deadline plus
cleanup; do not detach it. Bridge never retries and is idle when its command exits.

## Using Bridge from an application

An application needs no harness skill, SDK, registration, or Bridge code change.
Supply an initiator such as `my-app`: an ASCII label starting with a letter or
digit, then letters, digits, periods, underscores, or hyphens. It is a record
label, not authentication or authority. Use separate sessions for multiple targets.

Use a fixed argument list and absolute checkout working directory, never a
shell-built command string. Python can pass `user_selected_target` directly:

```python
import subprocess

checked = subprocess.run(
    ["python3", "-m", "bridge", "check", "--peer", user_selected_target],
    cwd="/absolute/path/to/agent-bridge",
    capture_output=True, text=True,
)
```

Show `checked.stdout` and `checked.stderr`; honor `checked.returncode`. Bridge
validates the target, so the application need not keep a copied target list.
Use the same subprocess pattern for session creation and notes, adding
`input=body` for complete nonempty Markdown. For a call, the arguments after
`bridge` are `run`, `--session`, and the absolute session path, optionally
`--timeout` and its value; pass `input=body` and read the response file only on
exit 0. Target and project come only from the session, not `run` arguments. A UI
displaying live warnings should drain both streams while keeping the process attached.

## Safety and warnings

Call only vendor CLIs you trust. Each is a program running under your own
account, not a confidentiality boundary: Bridge cannot stop it reading other
files that account can read. A project target may load `AGENTS.md`, `CLAUDE.md`,
or equivalent instructions. Vendor CLIs may keep plaintext transcripts; Bridge
neither suppresses repository instructions nor deletes or hides those logs.

Every connector uses the strongest practical vendor safeguards and warns about
remaining configuration, tool, and external-effect limits. Warnings do not
block a usable call, require approval, or store consent. Version or platform
drift may proceed with a warning when required switches and transport still
work. Complete confinement is not claimed: the model-provider connection and
same-user reads remain outside it. The [interface](INTERFACE.md) details the
individual connector limits, including surviving managed policy.

ZCode and Hermes receive the entire body as one bound command-line option,
visible to other same-user processes or potentially to system and vendor logs.
NUL and oversize argument bodies are refused before publication, never split
or truncated. The other four connectors use standard input. Bridge never
creates a private prompt file or treats a body as shell text.
Qwen receives one internal JSON user frame containing the unchanged body, then
end-of-input. Its stream reader avoids the text-input cutoff and the vendor's
text-to-command-line conversion; Bridge adds no prompt argument or file.

Bridge clears Qwen's inherited startup-argument overrides and pins its native macOS
sandbox selection and restrictive-open profile. Safe mode still loads settings
and `.env` values: they can restore `SANDBOX` and bypass that sandbox, or restore
`QWEN_SANDBOX_PROXY_COMMAND` and start a detached shell outside the sandbox and
Bridge's process group. Empty values cannot pin those routes off. These are
non-blocking warnings, not a claim of complete confinement.

Qwen Code 0.23.0 alone may preprocess recognized leading `/` commands or
unescaped `@` references: it may alter the effective prompt, append readable
file or resource content, fail before a model call, or handle a command itself.
Both headless modes share this; safe mode cannot disable it and no raw switch
exists. Qwen runs with `--max-tool-calls=0`: no model-initiated tool call can
execute, and the first such attempt aborts the run. Input preprocessing happens
before that budget, so the limit does not stop it. Bridge records and passes
the original body unchanged, warns during `check` and before publication, and
does not claim Qwen's model sees it unchanged. The other five prompts remain
lossless.

Missing software or minimum authentication, unusable input/output, missing required
mechanics, and an uncontrollable foreground process remain hard failures. Claude's
exact managed MCP source is incompatible with its strict-MCP invocation: a
prerequisite failure, distinct from the warning-only limits of other managed policy.

## Optional harness adapters and full interface

The six optional skill sources are [Codex](packages/codex/SKILL.md),
[Claude](packages/claude/SKILL.md), [ZCode](packages/zcode/SKILL.md),
[Hermes](packages/hermes/SKILL.md), [MiniMax](packages/minimax/SKILL.md), and
[Qwen](packages/qwen/SKILL.md). Each tells its host how to use the same checkout,
surface warnings and failures, send a body, read an answer, and record a note.
They are not separate runtimes and never call each other. Target-only harnesses
need no Bridge skill. These files neither install themselves nor update installed copies.

[INTERFACE.md](INTERFACE.md) defines the complete Format 2 command, record,
warning, qualification, failure, and cleanup contract and release criteria.
Application workflows, including Programming Loop, stay with the caller.

## License

SPDX-License-Identifier: Unlicense

This is free and unencumbered software released into the public domain. See
[UNLICENSE](UNLICENSE).

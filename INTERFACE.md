# Agent Bridge — Release 1 Courier Interface

**Status:** Release 1 courier contract, approved September 3, 2026. All six targets have passed real calls on macOS 26 arm64, but Qwen's corrected stream transport awaits its approved fresh qualification after local checks and independent review. The exercised CLI versions are listed in the README. Qualification does not by itself complete the release finish line in section 12.

This is the controlling boundary for the shared runner, six target connectors, and thin initiating adapters. It replaces the former exactly-four-target rule and the former rule that incomplete confinement stopped release.

SPDX-License-Identifier: Unlicense

---

## 1. Purpose and boundary

Agent Bridge is a standalone one-to-many Markdown courier. Any application or coding-agent harness may initiate a session and make one bounded call at a time to a supported target whose vendor CLI is installed, signed in, and qualified.

Agent Bridge owns:

- target readiness;
- one request and one response per foreground call;
- an ordered, human-readable Markdown record;
- the selected CLI's strongest practical restriction posture and clear warnings about what it cannot guarantee;
- one lock per session, atomic publication, deadlines, and cleanup; and
- plain failures with one useful next action when a call cannot work.

It does not plan, coordinate, choose targets, combine answers, schedule work, judge a response, run a Programming Loop, approve anything, manage Git, or enforce an application workflow. It has no model API layer, database, daemon, task ledger, plugin discovery, application registry, or dynamic connector registry.

Applications own the meaning of what they send. Ora Vibe Coder or any other caller may put plans, commits, reviews, decisions, or state in the Markdown body. To Agent Bridge those remain inert text.

### Same-user trust boundary

Agent Bridge treats each target CLI as a trusted program running under the user's operating-system account. It does not claim to stop that program reading other files the account can read, and is not a confidentiality boundary against a malicious harness or prompt injection.

A target given a project may load its `AGENTS.md`, `CLAUDE.md`, or equivalent instructions. Agent Bridge does not suppress them. Target CLIs may also keep plaintext transcripts; Agent Bridge neither deletes nor hides them.

Repository instructions and message text cannot alter the connector's fixed invocation, grant Bridge authority, or become a Bridge command. Users should call only harnesses they trust and should not use Bridge when same-user read access is unacceptable.

Bridge states remaining limits as warnings during `check` and immediately before a warned `run` publishes its request. Warnings are informational: they do not stop an otherwise usable call, ask for acknowledgment, require an approval switch, or create persistent consent.

---

## 2. Runtime and safe text transport

One standard-library Python implementation serves every initiator and target. Python 3.9 is the floor; release evidence names the tested operating-system versions and architectures.

Every child starts from a fixed argument vector with no shell. A connector uses standard input when its CLI has a stable one-shot input path. Otherwise it may bind the complete body into one vendor option value only when qualification proves:

1. Leading hyphens cannot become options.
2. The body is one argument in a directly executed vector, never a shell fragment.
3. NUL input and a body too large for the platform argument block are refused before request publication, never truncated or split.
4. Documentation states that command-line text may be visible to same-user processes and system or vendor logs.

The runner never creates a private prompt file. The canonical request records the original exactly and the connector passes it unchanged.

Qwen's connector internally encodes that body as one stream-JSON user frame whose decoded content is the original string, then closes standard input. It supplies no initial query, prompt argument, control request, history, or additional frame. This avoids Qwen's text-input conversion into its sandbox shell arguments. The public body remains plain Markdown; only the connector translates framing. Qwen's stream reader has no text-mode 8 MiB cutoff; no unlimited-memory guarantee is implied.

Adapters start Bridge as a fixed vector:

```text
python3 -m bridge <command> ...
```

---

## 3. Initiators and targets

### Initiators

Any application or harness may initiate. It supplies one inert ASCII slug beginning with a letter or digit and continuing with letters, digits, periods, underscores, or hyphens.

The label appears in `SESSION.md` and message headers. It is not authentication, authority, routing, discovery, registration, or proof that the initiator is callable. Examples include `ora`, `gear-3`, `vibe-coder`, `codex`, and `my-app`.

Supporting another initiator never requires a Bridge code change or release.

### Callable targets

Release 1 has exactly six target identifiers:

```text
codex   claude   zcode   hermes   minimax   qwen
```

They are literal source entries resolved by a branch-local six-way switch. Each branch matches its identifier before importing that connector. There is no eager import, name-derived import, search, registry, generator, marketplace, provider fallback, or attempt to try another target.

For `check` and `run`, every unselected connector remains completely inert: no import, inspection, probe, process, project access, login, network call, or fallback. Target validation during `record` imports no connector.

An initiator label may equal a target identifier, but that neither calls anything nor grants target status. Only the immutable `Peer:` session field selects a connector.

One session has one initiator and one target. An application reaches several targets through separate sessions; Bridge does not fan out or coordinate them. Codex, Claude Code, and ZCode are project-capable. Hermes Agent, MiniMax Code, and Qwen Code are courier-only and receive a task-owned neutral directory with no project path.

---

## 4. Commands

```text
python3 -m bridge check --peer <target-id>

python3 -m bridge run --session <session-directory>
                 [--timeout <seconds>]

python3 -m bridge record --session <session-directory>
                    --kind session-create
                    --initiator <label>
                    --peer <target-id>
                    [--project <project-directory>]

python3 -m bridge record --session <session-directory>
                    --kind note
```

Run these commands from the absolute checkout root. Source installation creates no `agent-bridge` console executable.

### `check`

`check` determines whether a target can be used now. Without a model call it finds the documented CLI, reads version and platform, checks authentication as far as the CLI safely permits, and confirms that the switches needed for fixed input, output, foreground control, and the connector's strongest practical posture still exist.

It runs in a task-owned neutral directory and never touches a real project, installs, signs in, selects a model or provider, or writes qualification state. Success writes one readiness sentence followed by any applicable `Warning:` lines to standard output, leaves standard error empty, and exits 0; warnings do not change that status. A hard failure writes one reason and next action to standard error and exits nonzero. No other connector is imported or examined.

### `run`

`run` reads the session's initiator, target, and optional project, then reads one nonempty Markdown body from standard input. It resolves only that target, repeats cheap prerequisites, validates transport, and determines current warnings. It writes any `Warning:` lines to standard error immediately before publishing the request, then starts exactly one bounded target CLI invocation, captures its final text, and atomically publishes the response.

Warnings never prompt, wait for acknowledgment, read an approval flag, or persist consent. A readable version outside exercised evidence may proceed with a warning when every required switch and the fixed one-shot transport remain usable.

Every run starts a fresh vendor context. Bridge neither resumes a vendor session nor sends earlier Bridge messages. An application needing history includes it in the current body.

`--timeout` is one deadline for prerequisites, target execution, and response capture, defaulting to 900 seconds. Cleanup has a separate bounded grace period. There is no retry.

A courier-only project session cannot run: Bridge refuses before connector import or request publication and tells the application to include evidence in the body or choose a project-capable target.

Success writes only the response path to standard output and exits 0. If a target fails after request publication, the request remains as an honest record, no response is invented, and Bridge writes the failure and next action to standard error and exits nonzero.

### `record`

`record` is the only local writer besides `run`. It creates a session or adds an application-neutral note without calling a target, using the shared validation, numbering, lock, envelope, and atomic writer.

Its substantive text comes from standard input. Empty or whitespace-only input is a usage error. Success prints the canonical path.

---

## 5. Neutral local records

`record` accepts exactly two kinds:

| Kind | Required arguments | Optional | Result |
|---|---|---|---|
| `session-create` | `--initiator <label>`, `--peer <target-id>` | `--project <dir>` | Creates `SESSION.md`; allocates no message number |
| `note` | none beyond session and kind | none | Creates one numbered initiator record |

The session body describes the session; a note may hold any application information. Bridge does not classify or interpret either.

`record` never invokes a target; creates, approves, replaces, or seals a plan; interprets workflow, review, repository, or Git state; changes the session's initiator; or creates application-specific headers.

Typed events belong in the application's Markdown body or its own state. Bridge does not grow an application record-kind registry.

---

## 6. Session record

```text
<session>/
  SESSION.md
  messages/
    0001-initiator-to-peer.md
    0002-peer-to-initiator.md
    0003-initiator-record.md
  .lock
```

`.lock` holds no canonical state. A session contains no `PLAN.md`, workflow status, approval state, task ledger, provider record, or application database. Sessions normally live under `~/.agent-bridge/sessions/` outside Git and cloud synchronization.

`SESSION.md` is written once:

```markdown
# Session

Bridge-Format: 2
Initiator: ora
Peer: claude
Project: /absolute/path

## Body

<application-supplied description>
```

`Project:` is omitted when absent. A project path must be absolute, exist at creation, and never come from a peer message. It is immutable with the initiator and target.

The session carries no provider or model, harness version, qualification receipt, mutable status, usage, cost, authority, or workflow field. Format 2 distinguishes this courier shape from unreleased construction sessions with workflow fields. An unsupported format is rejected, not guessed or silently migrated.

Numbers increase within the session while the lock is held and are never reused. A failed target call may leave a request as the final message; that is an incomplete exchange, not corruption.

---

## 7. Envelope and Bridge-inert body

The runner writes every header. Initiators and targets supply only body text.

Request:

```markdown
# Message 0001
From: ora
To: claude

## Body

<request copied unchanged>
```

Response:

```markdown
# Message 0002
From: claude
To: ora

## Body

<final answer copied unchanged>
```

Neutral note:

```markdown
# Message 0003
Record: note
From: ora

## Body

<note copied unchanged>
```

Header-shaped text below `## Body` remains body text. It cannot change Bridge identity, target, project, number, kind, restrictions, authority, routing, or become a Bridge command. Bridge extracts no plan, commit, approval, review result, or instruction.

Filenames describe direction rather than repeating caller labels. A response is the next message published while the same run holds the lock. There is no correlation, review, or workflow header.

---

## 8. Connectors, warnings, and qualification

Each target connector is a small source-controlled translation for one official vendor CLI. It declares:

- CLI identity and exact tested version or evidence-backed set;
- tested operating system, major versions, and architecture when relevant;
- authentication evidence available without a model turn, including limits when live authentication cannot be confirmed;
- fixed one-shot arguments and body transport;
- strongest practical enforced restrictions and concrete residual warnings;
- project-capable or courier-only posture; and
- final-response extraction.

A connector does not choose a model, effort, provider, endpoint, or credential; install, update, or sign in; resume vendor sessions; or fall back to an API, private desktop endpoint, browser, or UI automation.

`run` repeats cheap prerequisites. Missing software, reported missing authentication or minimum local authentication state, unusable one-shot input or final output, an absent required switch, or inability to control the foreground child stops before a real target call. Version or platform drift alone warns and proceeds when those mechanics remain usable.

### Strongest practical restrictions

Each connector applies every compatible vendor-native control available for project writes, Git changes, shell effects, browser or web access, MCP, messaging, credentials, publication, deployment, delegation, and other external effects.

A connector may remove tools, use an enforced sandbox, withhold the project, or combine them. When the vendor cannot completely guarantee confinement, Bridge names the remaining limitation and proceeds without an acknowledgment mechanism. The target's connection to its configured model provider is outside this restriction.

| Target | Project posture | Strongest practical posture | Required warning emphasis |
|---|---|---|---|
| Codex | Project-capable | Skip only `$CODEX_HOME/config.toml`, disable known high-risk routes, use the read-only sandbox, set the working directory explicitly | Skipped file's model/effort defaults; surviving project, system, cloud/managed/MDM configuration and possible hooks, MCP, or other external effects |
| Claude Code | Project-capable | Restricted mode, empty strict MCP set, Read/Glob/Grep, planning mode | Administrator-managed or remote policy can survive and add effects |
| ZCode | Project-capable | Planning mode, explicit directory, known dangerous tools removed | Plugin/direct-MCP limits, indirect OAuth evidence, visible body argument |
| Hermes Agent | Courier-only | Neutral directory, safe mode, smallest harmless toolset | Read cannot be separated from write, memory remains, visible body argument |
| MiniMax Code | Courier-only | Neutral directory, `exec` with standard input, `--permission smart`, one assistant step, native timeout within its supported range | Smart is discretionary, not a sandbox; one step does not disable tools; no state-free authentication check |
| Qwen Code | Courier-only | Neutral directory, safe and plan modes, pinned native macOS sandbox selection/profile, zero model tool calls, one input frame and turn, compatible time and pre-model command limits | Input preprocessing below; settings/.env may bypass sandbox or launch a detached proxy; profile permits same-user reads, some writes, process launches, and network; no safe no-turn authentication confirmation |

For Codex, `--ignore-user-config` does only what its name understates: it skips `$CODEX_HOME/config.toml`. It does not suppress trusted-project `.codex/config.toml` files and project hooks or rules, system configuration, `managed_config.toml`, `requirements.toml`, cloud-delivered requirements, macOS MDM preferences, or separately sourced user/global hooks and rules. Those surviving layers can add settings the fixed vector does not override, and managed defaults or MDM can override CLI options. Hooks, MCP servers, plugins, network or telemetry settings, and other integrations from surviving configuration may therefore retain routes to external effects outside the read-only shell sandbox. Bridge names that limit in its non-blocking warning. The skipped file's model and effort defaults are also lost, and Bridge does not replace them.

**Claude Code 2.1.251 managed MCP prerequisite.** The exact source `/Library/Application Support/ClaudeCode/managed-mcp.json` is incompatible with the fixed `--strict-mcp-config` invocation: the CLI exits when they are combined. If that path is present or its absence cannot be established, `check` fails and `run` fails before request publication. Bridge observes the path without opening policy contents. This is an unusable-command prerequisite, not a refusal over incomplete confinement. Other administrator-managed endpoint and remote policy may survive restricted mode; their presence or uncertainty remains a warning, not this hard failure.

**Qwen Code 0.23.0 input exception.** Selected Qwen may interpret recognized leading `/` commands or unescaped `@` references before the model. It may alter or replace the effective prompt, read and append readable file or resource content, fail in preprocessing, or handle a command without a model call. Both supported headless input modes share this; safe mode cannot disable it and no lossless escape or raw switch exists. Bridge records and passes the original exactly, gives Qwen a task-owned neutral directory with no project, and requires `--max-tool-calls=0`: no model-initiated tool call can execute, and the first such attempt aborts the run. Input preprocessing happens before that budget, so the limit does not stop it. Bridge warns during `check` and before publication without blocking or acknowledgment. The other five prompts remain lossless and unselected Qwen inert. An official raw mode would make the exception removable after qualification.

The Qwen vector disables `/bug`, `/config`, `/update`, `/import-config`, `/language`, `/effort`, `/model`, and `/doctor` because those pre-model command families can cause external or persistent effects. Other recognized preprocessing remains. Qwen retains the user's existing authentication and provider setup; Bridge adds no selection or credential handling.

For both readiness and runs, Bridge clears inherited `QWEN_CODE_RELAUNCH_ARGS` so it cannot replace the fixed startup arguments. It pins nonempty `QWEN_SANDBOX=sandbox-exec` and `SEATBELT_PROFILE=restrictive-open`, and removes inherited `SANDBOX` and `QWEN_SANDBOX_PROXY_COMMAND`. Safe mode still loads settings and `.env` values, which can refill missing or empty variables: restored `SANDBOX` can bypass the sandbox, and a restored proxy command can launch a detached shell outside both the sandbox and Bridge's process group. Empty strings cannot disable those routes reliably. Bridge names these surviving routes as non-blocking warnings; it does not inspect or alter the user's configuration or add a confinement gate.

### Disposable qualification

Before real project use, a task-owned synthetic Git repository proves:

1. A project-capable target reads supplied evidence; a courier-only target receives no project.
2. Local create, modify, delete, Git-ref, and repository-configuration attempts occur only in that disposable repository, whose tracked content, untracked files, `HEAD`, refs, configuration, and clean status are compared afterwards.
3. No `.git` lock or task-owned child remains.
4. No prompt deliberately attempts browser or web access, messaging, MCP service calls, credential access or change, publication, deployment, login, purchase, or another real-world effect. Those routes are described from the fixed vector, safe no-turn metadata, and uninvoked tool inventory.
5. The exact record and CLI transport preserve leading hyphens, Unicode, multiline text, and the complete response. Qwen uses non-triggering content and claims no raw prompt.
6. The temporary parent is removed on every exit path.

The test uses no real project, secret, message, production service, or publication. Same-user reads outside the project are not claimed to be confined.

Qualification is source evidence, not mutable runtime state. There is no last-passed stamp, cache, receipt, database, or third connector operation. A CLI outside declared evidence warns when the required mechanics still work; qualification updates source rather than granting per-user approval.

---

## 9. Locking, publication, and cleanup

One foreground turn holds a process-scoped advisory lock from sequence allocation through response publication or cleanup. Contention changes no canonical file. There is no lease, heartbeat, stale-lock service, or lock stealing.

Each canonical file is completed in a temporary file in its destination directory, flushed, and atomically renamed. A partial file is never published. An uncertain rename reports the exact canonical path and requires inspection before another run.

Each child belongs to the turn's process group. Timeout, interrupt, termination, and hangup terminate and reap it, remove task-owned temporary files, and release the lock. Cleanup failure visibly names what remains.

`SIGKILL` and power loss cannot clean up. A child that escapes its process group fails qualification if observed. Outside the main thread, the surrounding program's signal behavior applies because handlers cannot be installed. Storage failure can still make publication uncertain.

There is no automatic retry. Retrying an uncertain provider call is an application decision.

---

## 10. Internal failures

The core owns this internal list; connectors map vendor behavior into it. The names are not a public compatibility surface.

| Failure | Meaning and next action |
|---|---|
| `MISSING_CLI` | Target CLI absent; install its official program and check again. |
| `AUTHENTICATION_REQUIRED` | Supported sign-in not observed; sign in through the harness. |
| `UNREPORTABLE_VERSION` | Version unreadable; inspect the vendor command. |
| `UNQUALIFIED_VERSION` | Retained internal diagnostic; current connectors warn on readable version drift when required mechanics remain usable. |
| `UNQUALIFIED_PLATFORM` | Retained internal diagnostic; current connectors warn on platform drift when required mechanics remain usable. |
| `RESTRICTIONS_UNAVAILABLE` | A required fixed-vector, input, output, or foreground switch is absent; the promised call cannot be made. |
| `QUALIFICATION_UNSAFE_OR_INCONCLUSIVE` | Retained internal diagnostic, not a current runtime confinement gate; residual limits are warnings. |
| `BUSY_SESSION` | Another turn owns the lock; wait. |
| `TIMEOUT` | Deadline expired; inspect visible state before deciding whether to retry. |
| `PEER_FAILURE` | Vendor CLI failed; correct the harness-side problem. |
| `EMPTY_RESPONSE` | No final text; check the target directly. |
| `CLEANUP_FAILURE` | A task-owned process or path remains; remove the named item. |
| `USAGE_ERROR` | Argument, label, body, target/project combination, or transport invalid; correct it. |
| `UNKNOWN_HARNESS` | Target is not one of the six fixed identifiers; name one. |
| `CONNECTOR_UNAVAILABLE` | Incomplete build lacks a fixed target's connector; use complete source. All six ship in Release 1. |
| `UNKNOWN_RECORD_KIND` | Kind is not `session-create` or `note`; use one of them. |
| `SESSION_NOT_FOUND` | Session absent; create it or correct the path. |
| `SESSION_INVALID` | Session unreadable, inconsistent, or unsupported; inspect it or start again. |
| `SESSION_EXISTS` | Session already exists; continue it or choose an empty path. |
| `PUBLICATION_FAILURE` | Nothing published; correct storage and retry only when safe. |
| `PUBLICATION_NOT_FLUSHED` | File exists but directory entry was not forced to disk; treat as unfinished. |
| `PUBLICATION_UNCERTAIN` | Rename outcome unknown; inspect the exact path before anything else. |

Every command reports failures on standard error and exits nonzero. A failure avoids false success, cleans what the turn owns when possible, and supplies one next action.

---

## 11. Initiating adapters

An adapter may be a harness skill, local application wrapper, or server-side integration running where target CLIs and vendor sign-ins exist. It:

1. Supplies an inert initiator label and fixed target.
2. Creates or reuses the one-initiator, one-target session.
3. Hands one complete Markdown body to `run`.
4. Reads the returned response path.
5. Adds a neutral note when needed.
6. Reports readiness, warnings, and failures without hiding them.

The adapter owns its UI, target-selection policy, multi-target work, context assembly, plans, approvals, reviews, corrections, Git behavior, retry decisions, and response interpretation. Those remain application responsibilities even inside a harness package.

The six harness packages expose courier, readiness, and neutral-record entry points through host conventions. They contain no planning, Programming Loop, or review product, never find or call each other, and invoke the one shared Bridge.

A target-only harness needs no Bridge package—only its official CLI, vendor sign-in, and a qualified connector. Adding an application needs no registration, connector, generated package, or Bridge release. Adding a target requires a stable official CLI, hand-written literal connector, qualification, focused checks, and current documentation.

---

## 12. Release 1 conformance

Release 1 conforms only when evidence proves:

1. Targets are exactly `codex`, `claude`, `zcode`, `hermes`, `minimax`, and `qwen`; another target fails, new valid initiator labels work without registration, and all five unselected connectors stay inert.
2. Each connector reports accurate readiness and concrete non-blocking warnings without a model turn, then completes one distinctive Markdown round trip.
3. Safe disposable qualification proves declared posture, transport, local state, and cleanup without deliberately attempting a real-world effect; courier-only targets refuse projects before connector import and publication.
4. A fake target proves both record kinds, inert bodies, immutable sessions, numbering, atomic publication, contention, timeout, failures, interruption, child termination, and no orphan.
5. Each of six harness adapters and one arbitrary application adapter can create, check, send, receive, record a note, and report warnings and failures.
6. Inspection finds no model API, dynamic registry, pair bridge, coordinator, router, scheduler, database, daemon, workflow engine, Git gate, plan store, approval or acknowledgment mechanism, review protocol, or Programming Loop.
7. Documentation matches the tested implementation, platform, and versions.

The complete authorized commands are:

```text
/usr/bin/python3 -m unittest -v tests.test_fake_peer
python3 -m tests.release_conformance inspect
python3 -m tests.release_conformance adapters
python3 -m tests.release_conformance qualify --peer codex
python3 -m tests.release_conformance qualify --peer claude
python3 -m tests.release_conformance qualify --peer zcode
python3 -m tests.release_conformance qualify --peer hermes
python3 -m tests.release_conformance qualify --peer minimax
python3 -m tests.release_conformance qualify --peer qwen
```

Each qualification includes readiness and one distinctive real model call. The approved transport correction adds one Qwen-only qualification after local checks and independent review. No full suite, build, benchmark, all-pairs test, other repeated qualification, deliberate external-effect attempt, or duplicate reassurance pass is part of the ceiling. Application behavior is outside this boundary.

Release finishes only after accepted source is committed, pushed, reviewed in a pull request, merged to `main`, and the existing repository is public. The installed Claude adapter is updated from merged source, anonymous repository installation is verified, task-owned resources are removed, and the Ora task receives the actual merged commit and invocation path. Bridge itself installs no target CLI and signs no user in.

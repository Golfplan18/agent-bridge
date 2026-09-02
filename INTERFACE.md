# Agent Bridge — Frozen Courier Interface

**Status:** Release 1 target contract, approved September 2, 2026. The implementation is under construction and may not yet conform.

This is the fixed boundary for the shared runner, four target connectors, and thin initiating adapters. Change it only when evidence shows that a required transport or safety property is otherwise impossible.

SPDX-License-Identifier: Unlicense

---

## 1. Purpose and boundary

Agent Bridge is a standalone one-to-many Markdown courier. Any application or coding-agent harness may initiate a session and make one bounded call at a time to a supported target whose vendor CLI is installed, signed in, and qualified.

Agent Bridge owns:

- target readiness;
- one request and one response per foreground call;
- an ordered, human-readable Markdown record;
- a least-authority target invocation;
- one lock per session, atomic publication, deadlines, and cleanup; and
- plain failures with one useful next action.

It does not plan, coordinate, choose targets, combine answers, schedule work, judge a response, run a Programming Loop, approve anything, manage Git, or enforce an application workflow. It has no model API layer, database, daemon, task ledger, plugin discovery, application registry, or dynamic connector registry.

Applications own the meaning of what they send. Ora Vibe Coder or any other caller may put plans, commits, reviews, decisions, or state in the Markdown body. To Agent Bridge those remain inert text.

### Same-user trust boundary

Agent Bridge treats each target CLI as a trusted program running under the user's operating-system account. It does not claim to stop that program reading other files the account can read, and is not a confidentiality boundary against a malicious harness or prompt injection.

A target given a project may load its `AGENTS.md`, `CLAUDE.md`, or equivalent instructions. Agent Bridge does not suppress them. Target CLIs may also keep plaintext transcripts; Agent Bridge neither deletes nor hides them.

Repository instructions and message text cannot alter the connector's enforced restrictions, grant Bridge authority, or become a Bridge command. Users should call only harnesses they trust and should not use Bridge when same-user read access is unacceptable.

---

## 2. Runtime and safe text transport

One standard-library Python implementation serves every initiator and target. Python 3.9 is the floor; release evidence names the tested operating-system versions and architectures.

Every child starts from a fixed argument vector with no shell. A connector uses standard input when its CLI has a stable one-shot input path. Otherwise it may bind the complete body into one vendor option value only when qualification proves:

1. Leading hyphens cannot become options.
2. The body is one argument in a directly executed vector, never a shell fragment.
3. NUL input and a body too large for the platform argument block are refused before request publication, never truncated or split.
4. Documentation states that command-line text may be visible to same-user processes and system or vendor logs.

The runner never creates a private prompt file for a target to discover. The canonical request records what was sent; the connector delivers its body through the qualified channel.

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

Release 1 has exactly four target identifiers:

```text
codex   claude   zcode   hermes
```

They are literal source entries resolved by an explicit switch. There is no search, registry, generator, marketplace, or provider fallback. Adding a target is a deliberate source change backed by qualification evidence.

An initiator label may equal a target identifier, but that neither calls anything nor grants target status. Only the immutable `Peer:` session field selects a connector.

One session has one initiator and one target. An application reaches several targets through separate sessions; Bridge does not fan out or coordinate them.

---

## 4. Commands

```text
agent-bridge check --peer <target-id>

agent-bridge run --session <session-directory>
                 [--timeout <seconds>]

agent-bridge record --session <session-directory>
                    --kind session-create
                    --initiator <label>
                    --peer <target-id>
                    [--project <project-directory>]

agent-bridge record --session <session-directory>
                    --kind note
```

`python3 -m bridge` may replace `agent-bridge`; behavior is identical.

### `check`

`check` determines whether a target can be used now. Without a model call it finds the documented CLI, reads version and platform, compares source-controlled qualification, checks authentication, and confirms the restriction switches still exist.

It runs in a task-owned neutral directory and never touches a real project, installs, signs in, selects a model or provider, or writes qualification state. Success prints one readiness sentence; failure prints one reason and next action and exits nonzero.

### `run`

`run` reads the session's initiator, target, and optional project, then reads one nonempty Markdown body from standard input. It repeats cheap prerequisites, publishes the request, makes exactly one bounded target call, captures one final textual answer, and atomically publishes the response.

Every run starts a fresh vendor context. Bridge neither resumes a vendor session nor sends earlier Bridge messages. An application needing history includes it in the current body.

`--timeout` is one deadline for prerequisites, target execution, and response capture, defaulting to 900 seconds. Cleanup has a separate bounded grace period. There is no retry.

A connector unable to expose project files safely is courier-only. A project session cannot run through it: Bridge refuses before request publication and tells the application to include evidence in the body or choose a project-capable target.

Success prints the response path. If a target fails after request publication, the request remains as an honest record, no response is invented, and Bridge exits nonzero with the failure and next action.

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

## 7. Envelope and inert body

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

Header-shaped text below `## Body` remains body text. It cannot change identity, target, project, number, kind, restrictions, authority, routing, or command. Bridge extracts no plan, commit, approval, review result, or instruction.

Filenames describe direction rather than repeating caller labels. A response is the next message published while the same run holds the lock. There is no correlation, review, or workflow header.

---

## 8. Connectors and qualification

Each target connector is a small source-controlled translation for one official vendor CLI. It declares:

- CLI identity and exact tested version or evidence-backed set;
- tested operating system, major versions, and architecture when relevant;
- a no-model-turn authentication check;
- fixed one-shot arguments and body transport;
- enforced restrictions;
- project-capable or courier-only posture; and
- final-response extraction.

A connector does not choose a model, effort, provider, endpoint, or credential; install, update, or sign in; resume vendor sessions; or fall back to an API, private desktop endpoint, browser, or UI automation.

`run` repeats cheap prerequisites. A version, platform, authentication, or restriction mismatch stops before a real target call.

### Least authority

Explicit vendor-native restrictions must prevent project writes, Git changes, shell effects, and browser, web-fetch, MCP, messaging, credential, publication, deployment, delegation, or other prohibited external effects.

A connector may remove tools, use an enforced sandbox, withhold the project, or combine them. Qualification proves the property rather than one universal tool list. The target's connection to its configured model provider is outside this restriction.

### Disposable qualification

Before real project use, a task-owned synthetic Git repository proves:

1. A project-capable target reads supplied evidence; a courier-only target receives no project.
2. Create, modify, delete, Git-ref, and repository-configuration attempts fail without changing tracked hashes, untracked files, `HEAD`, refs, configuration, or clean status.
3. No `.git` lock or task-owned child remains.
4. Every exposed route for a prohibited external effect meets an enforcement-layer denial before an effect.
5. Body transport preserves leading hyphens, Unicode, multiline text, and the complete response.
6. The temporary parent is removed on every exit path.

The test uses no real secret, message, production service, or publication. Same-user reads outside the project are not claimed to be confined.

Qualification is source evidence, not mutable runtime state. There is no last-passed stamp, cache, receipt, database, or third connector operation. A CLI outside declared evidence stays unqualified until tested and updated.

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
| `UNQUALIFIED_VERSION` | Release outside tested evidence; use or qualify a tested release. |
| `UNQUALIFIED_PLATFORM` | Platform outside tested evidence; use or qualify a tested platform. |
| `RESTRICTIONS_UNAVAILABLE` | Required enforcement absent; do not call until requalified. |
| `QUALIFICATION_UNSAFE_OR_INCONCLUSIVE` | Disposable proof failed or was ambiguous; inspect and requalify. |
| `BUSY_SESSION` | Another turn owns the lock; wait. |
| `TIMEOUT` | Deadline expired; inspect visible state before deciding whether to retry. |
| `PEER_FAILURE` | Vendor CLI failed; correct the harness-side problem. |
| `EMPTY_RESPONSE` | No final text; check the target directly. |
| `CLEANUP_FAILURE` | A task-owned process or path remains; remove the named item. |
| `USAGE_ERROR` | Argument, label, body, target/project combination, or transport invalid; correct it. |
| `UNKNOWN_HARNESS` | Target is not one of the four fixed identifiers; name one. |
| `CONNECTOR_UNAVAILABLE` | Fixed target lacks a connector in this construction build; complete it or use another. |
| `UNKNOWN_RECORD_KIND` | Kind is not `session-create` or `note`; use one of them. |
| `SESSION_NOT_FOUND` | Session absent; create it or correct the path. |
| `SESSION_INVALID` | Session unreadable, inconsistent, or unsupported; inspect it or start again. |
| `SESSION_EXISTS` | Session already exists; continue it or choose an empty path. |
| `PUBLICATION_FAILURE` | Nothing published; correct storage and retry only when safe. |
| `PUBLICATION_NOT_FLUSHED` | File exists but directory entry was not forced to disk; treat as unfinished. |
| `PUBLICATION_UNCERTAIN` | Rename outcome unknown; inspect the exact path before anything else. |

Every failure avoids false success, cleans what the turn owns when possible, and supplies one next action.

---

## 11. Initiating adapters

An adapter may be a harness skill, local application wrapper, or server-side integration running where target CLIs and vendor sign-ins exist. It:

1. Supplies an inert initiator label and fixed target.
2. Creates or reuses the one-initiator, one-target session.
3. Hands one complete Markdown body to `run`.
4. Reads the returned response path.
5. Adds a neutral note when needed.
6. Reports readiness and failures without hiding them.

The adapter owns its UI, target-selection policy, multi-target work, context assembly, plans, approvals, reviews, corrections, Git behavior, retry decisions, and response interpretation. Those remain application responsibilities even inside a harness package.

The four harness packages expose courier, readiness, and neutral-record entry points through host conventions. They contain no planning, Programming Loop, or review product, never find or call each other, and invoke the one shared Bridge.

A target-only harness needs no Bridge package—only its official CLI, vendor sign-in, and a qualified connector. Adding an application needs no registration, connector, generated package, or Bridge release. Adding a target requires a stable official CLI, hand-written literal connector, qualification, focused checks, and current documentation.

---

## 12. Release 1 conformance

Release 1 conforms only when evidence proves:

1. Targets are exactly `codex`, `claude`, `zcode`, and `hermes`; another target fails while new valid initiator labels work without registration.
2. Each connector reports accurate readiness without a model turn and completes one distinctive Markdown round trip.
3. Disposable qualification proves declared platform, transport, non-mutation, prohibited-effect restrictions, and cleanup; courier-only targets refuse projects before publication.
4. A fake target proves both record kinds, inert bodies, immutable sessions, numbering, atomic publication, contention, timeout, failures, interruption, child termination, and no orphan.
5. Each harness adapter and one arbitrary application adapter can create, check, send, receive, record a note, and report failure.
6. Inspection finds no model API, dynamic registry, pair bridge, coordinator, router, scheduler, database, daemon, workflow engine, Git gate, plan store, approval mechanism, review protocol, or Programming Loop.
7. Documentation matches the tested implementation, platform, and versions.

Application behavior is outside this boundary. No planning, Programming Loop, review-quality, Git, or Ora Vibe Coder test may gate Agent Bridge Release 1.

# Agent Bridge — Frozen Interface

**This document is frozen.** Everything below is settled before the separate
workstreams start building, so that five connectors, five native packages and
one shared runner can be written at the same time without arguing about shapes
later. It may change only when evidence shows that a required behavior or safety
property is otherwise impossible — not because something here turns out to be
inconvenient, and not to accommodate one harness's habits.

SPDX-License-Identifier: Unlicense

---

## 1. What Agent Bridge is, and the boundary it keeps

Agent Bridge lets two coding-agent harnesses work on one job together. It calls
them through the official command-line programs their vendors publish. It does
not call model APIs, and it has no opinion about which model is behind either
program.

What the bridge owns is deliberately small: one bounded call to a peer harness,
an ordered exchange of Markdown files on disk, an explicit least-authority
invocation, one lock per session, publication that either completes or does not
happen at all, cleanup of everything the turn started, and the written
instructions that coordinate planning and review.

Everything else stays where it already lives. Each harness keeps its own
authentication, subscription, providers, models, tools, agents and native
sessions, and does its own implementation work its own way. The project
repository and Git remain the record of what was built and the way to undo it.
The bridge adds no second ledger, no database, no daemon, no scheduler and no
background service. When the foreground command exits, Agent Bridge is idle.

---

## 2. The shared runtime

One implementation of the runner serves all five harnesses. That is only
possible if it runs anywhere, so:

- **Python standard library only.** No third-party runtime dependency, in the
  runner, in the connectors, or in the checks and fixtures.
- **Python 3.9 and later.** macOS ships 3.9 as the system Python, so that is the
  floor; the code must also run unchanged on current releases.
- **Started as a fixed argument vector, never through a shell.** Every program
  the bridge starts — a peer harness, or Git — is launched with an explicit list
  of arguments and no shell interpretation.
- **The body always arrives on standard input.** The outgoing Markdown text is
  written to the peer program's standard input. Prompt text never appears on a
  command line, in an environment variable, or in a shell string.

The last two points are one safety property stated twice: text that a peer or a
plan may have influenced never becomes part of a command.

---

## 3. The five harnesses

Agent Bridge knows exactly five harnesses, identified by these fixed names:

```text
codex   claude   zcode   hermes   minimax-code
```

The list is written out in source, in that order. There is no discovery, no
plugin search, no registry and no marketplace: adding a sixth harness means
editing the file that names them, which is the point — the complete set of
programs the bridge is willing to start is visible by reading it.

---

## 4. The three commands

```text
agent-bridge check --peer <harness-id>

agent-bridge run --peer <harness-id> --session <session-directory>
                 [--project <project-directory>]
                 [--review-base <commit> --review-head <commit>]
                 [--timeout <seconds>]

agent-bridge record --session <session-directory>
                    --kind <session-create|user-correction|plan-approval|
                            technical-error|implementation-start|user-waiver>
                    [kind-specific runner-owned fields]
```

**`check`** answers one question: can this peer be used right now? It finds the
documented program, reads its version and platform, compares them with what the
connector claims to have been tested against, confirms the harness is signed in,
and confirms the restriction switches it needs are actually there. It touches no
real project. It never installs anything, never logs anyone in, never picks a
model or a provider, and writes nothing down for next time.

**`run`** performs one bounded turn: it publishes the outgoing body as the next
message, starts the peer once, waits for one answer, and publishes that answer
or a visible failure. Supplying `--review-base` and `--review-head` — which are
required together — turns the turn into an external review and switches on the
exact-commit safeguards described in section 8. `--timeout` is one deadline
covering the whole turn: prechecks, generating review evidence, the peer call
and capturing the response. Cleanup afterwards gets its own bounded grace
period. There is no retry.

**`record`** writes one local message into the session without calling anybody.
It is described in the next section.

For all three: a technical failure exits nonzero and prints one plain reason and
one next action. Success prints the path of the canonical file that was written.

---

## 5. The `record` command

`record` exists so that a native package never has to write a session file
itself. The package hands the runner some text and says what kind of record it
is; the runner does the numbering, the locking, the envelope and the atomic
write. This keeps one writer for the canonical record, whichever harness is
driving.

It accepts exactly six kinds and nothing else. Every kind reads its substantive
Markdown body from standard input; input that is empty or only whitespace is a
usage error.

| Kind | Required fields | Optional | What it writes |
|---|---|---|---|
| `session-create` | `--local <harness-id>`, `--peer <harness-id>`, `--workflow <planning\|programming-loop\|external-review>` | `--project <dir>` | `SESSION.md` only; no message number is allocated |
| `user-correction` | none | none | one numbered local record |
| `plan-approval` | none | `--replace` | one numbered local record holding the approved plan text, then `PLAN.md` with the same text |
| `technical-error` | none | none | one numbered local record |
| `implementation-start` | `--project <dir>`, `--baseline <commit>` | none | one numbered local record carrying the repository identity the runner resolved and the full baseline commit |
| `user-waiver` | `--project <dir>`, `--head <commit>`, `--waived <REJECT\|ERROR>` | none | one numbered local record |

Two kinds have extra rules. `plan-approval` seals `PLAN.md`; sealing over an
already approved plan requires `--replace`, and the earlier approved text stays
readable in its own numbered message. `implementation-start` may happen at most
once in a session, because the repository and baseline it seals are what every
later review is bound to.

What `record` can never do is as important as what it does:

- it never invokes a peer harness;
- it never writes a `Review-Request`, `Review-Base` or `Review-Head` field;
- it never creates an external-review response and never produces a verdict;
- it accepts no kind outside the six above.

Only a review-mode `run` can produce an external `ACCEPT`, `REJECT` or
`ASK_USER`. A local record can carry a user's waiver — which is a different
authority, reported differently — but it cannot manufacture an acceptance.

---

## 6. The session record

A session is a folder. Everything the bridge knows is in it, in files a person
can read:

```text
<session>/
  SESSION.md
  messages/
    0001-local-to-peer.md
    0002-peer-to-local.md
    0003-local-record.md
  PLAN.md
  .lock
```

Message files are named `NNNN-local-to-peer.md`, `NNNN-peer-to-local.md` or
`NNNN-local-record.md`, where `NNNN` is a sequence number padded to at least four
digits. `.lock` is only something to hold a lock on; it holds no state, and
deleting it destroys nothing. `PLAN.md` appears once the user has approved a
plan.

Sessions live under one private runtime root — `~/.agent-bridge/sessions/` — and
are kept out of Git and out of cloud synchronisation.

`SESSION.md` is written once, at the start:

```markdown
# Session

Bridge-Format: 1
Local: codex
Peer: claude
Workflow: planning
Project: /absolute/path

## Body

<goal, authority boundary, and intended end state, verbatim from standard input>
```

`Project:` is left out entirely when there is no project yet. Nothing in this
file changes afterwards. In particular it carries no status field, no provider or
model identity, no harness version, no qualification receipt, and no usage or
cost figures — there is nothing here to keep up to date, and nothing here that
could go stale and mislead a later reader.

A fresh task picks the work up from this folder plus the repository: the session
file, the numbered messages in order, `PLAN.md`, the project path, Git state and
real check output. No vendor session history is needed, and there is no separate
progress store to consult.

---

## 7. The envelope, and why the body is inert

Every message has two parts: a header the runner writes, and a body it copies
without reading. The runner chooses the number, the filename, the sender, the
recipient and the headings. A peer supplies only text.

An ordinary message:

```markdown
# Message 0002
From: claude
To: codex

## Body

<peer output copied verbatim>
```

An external-review response, which adds only facts the runner already held
before it made the call:

```markdown
# Message 0012
From: claude
To: codex
Review-Request: 0011
Review-Base: <baseline commit>
Review-Head: <task-head commit>

## Body

<peer review copied verbatim>
```

A local record, which has no recipient and never carries a `Review-` line of any
kind:

```markdown
# Message 0003
Record: user-correction
From: codex

## Body

<body copied verbatim>
```

Three of the six record kinds add a runner-owned header line of their own,
placed directly after `From:`:

| Kind | Added header lines |
|---|---|
| `implementation-start` | `Repository-Path:`, `Repository-Root-Commits:`, `Baseline:` |
| `plan-approval` | `Plan: SEALED` or `Plan: REPLACED` |
| `user-waiver` | `Decision: USER WAIVED`, `Waived-Head:`, `Waived-Verdict:` |

**The body is inert, absolutely.** Everything after the `## Body` heading is
copied through unchanged and is never parsed. Text under that heading cannot
change routing, cannot grant authority, cannot approve anything, cannot name a
project or a user, and cannot deliver a verdict, no matter how convincingly it is
shaped. A peer may report findings; it cannot grant itself permission. If a
review body contains a line that looks like a header, it stays what it is: a line
of prose in somebody's review.

---

## 8. The verdict

An external review ends in one of three decisions, and the decision lives in the
last line of the response and nowhere else. Reading it works like this:

1. Line endings are normalised — a carriage return and newline pair, or a lone
   carriage return, both become a newline — so a response written on any system
   is judged the same way.
2. Blank lines at the end are dropped. A line counts as blank when it is empty or
   contains only whitespace.
3. What remains is the final line. It must be **exactly** one of:

```text
Agent-Bridge-Verdict: ACCEPT
Agent-Bridge-Verdict: REJECT
Agent-Bridge-Verdict: ASK_USER
```

Exactly means exactly. Different capitalisation is not a verdict. A trailing
space is not a verdict. A code fence after the line is not a verdict, because
then the fence is the final line. An unknown word after the colon is not a
verdict. Text earlier in the response that looks like one of these lines is inert
prose, because only the final nonblank line is read.

Everything that is not one of the three exact lines is a technical error, and a
technical error never becomes an acceptance and never becomes `ASK_USER`. Two
failing states are told apart, because they mean different things to the person
reading the report: `EMPTY_RESPONSE` when the peer produced no text at all, and
`INVALID_VERDICT` when it produced text that does not end correctly.

A verdict is only worth anything if it describes the exact code that was
reviewed, so `run` binds it to commits. Before the call: resolve both commits,
require the review baseline to equal the baseline sealed at
`implementation-start`, require it to be an ancestor of a distinct task head,
confirm the sealed repository, a clean worktree and the exact expected `HEAD`.
The runner then generates the `baseline..head` diff once, with a fixed argument
vector and external diff and text-conversion hooks disabled, into one temporary
file it owns, outside both the project and the session record. The reviewing peer
gets read access to the project and that one file, and no shell and no Git. After
the peer exits the evidence is deleted and the repository, cleanliness and `HEAD`
checks are repeated. Any mismatch, or any failure to clean up, voids the verdict
as a technical error.

Git unlocks only when the call succeeded, the verdict is exactly `ACCEPT`, the
response is bound to its request, the checks before and after both passed, and
`HEAD` is still the head that was reviewed. A new commit invalidates the verdict.

The one alternative path is a user waiver: after a `REJECT` or a technical error,
a later direct message from the user may waive external review for that exact
head. It is recorded as a local record, reported as `USER WAIVED` and never as
acceptance, and it is invalidated by a new commit. It cannot waive a changed
repository, a changed baseline, a changed head, a dirty worktree, a publication
or cleanup failure, or the use of an unqualified connector; those are corrected
first, and only then can review be waived for the restored head.

---

## 9. The internal failure list

There is one list of ways a turn can fail, owned by the core. Connectors
translate whatever a vendor's program did into exactly one member of it, and may
not invent codes of their own. Only the runner turns a member into words, so
every failure reaches a person in the same shape: what happened, and the one
thing to do next.

**This list is internal.** It is not a public protocol, not a wire format and not
a compatibility surface for anybody else's software. Nothing outside this
repository should depend on these names, and they may be renamed whenever the
code needs it.

| Member | What it means | Next action |
|---|---|---|
| `MISSING_CLI` | The peer's command-line program is not on this computer | Install the official program, put it on `PATH`, check readiness again |
| `AUTHENTICATION_REQUIRED` | The program is there but nobody is signed in | Sign in with that harness's own login command |
| `UNREPORTABLE_VERSION` | The program printed no readable version | Run its version command by hand; if it stays unreadable the harness cannot be qualified |
| `UNQUALIFIED_VERSION` | The installed version is outside the tested set | Install a tested version, or requalify and update the connector's declaration |
| `UNQUALIFIED_PLATFORM` | This operating system or major version is outside the tested coverage | Use a tested platform, or requalify there and update the declaration |
| `RESTRICTIONS_UNAVAILABLE` | The harness lacks the switches needed to deny writes and outside reads | Do not give it real project access; report the missing restriction |
| `QUALIFICATION_UNSAFE_OR_INCONCLUSIVE` | The disposable probe did not clearly prove the boundary held | Read the reported synthetic path, work out what happened, requalify |
| `BUSY_SESSION` | Another turn holds this session's lock | Wait for it to finish and run again; nothing was changed |
| `TIMEOUT` | The deadline passed before an answer arrived | Run again with a longer `--timeout`, or check the peer by hand |
| `PEER_FAILURE` | The peer's program exited with a failure | Read its own error output and fix it inside that harness |
| `EMPTY_RESPONSE` | The peer produced no text at all | Check the peer by hand and run again; Git stays locked |
| `INVALID_VERDICT` | A review response did not end with one of the three exact lines | Run the review again; this is never an acceptance |
| `REPOSITORY_CHANGED` | The repository is not the one sealed at implementation start | Point at the sealed repository, or start a new session |
| `BASELINE_CHANGED` | The review baseline is not the sealed baseline | Run the review again with the sealed baseline |
| `HEAD_CHANGED` | The branch moved, so the review no longer describes the code | Run a fresh review against the current head |
| `CLEANUP_FAILURE` | Something the turn created could not be removed | Remove the reported path or process by hand and confirm nothing is left |
| `USAGE_ERROR` | Missing, conflicting or empty arguments, including empty input | Correct the command line and the input, then run again |
| `UNKNOWN_HARNESS` | The named harness is not one of the five | Name one of the five identifiers |
| `CONNECTOR_UNAVAILABLE` | A real identifier, but no connector for it ships in this build | Use a harness whose connector ships |
| `UNKNOWN_RECORD_KIND` | Not one of the six record kinds | Name one of the six kinds |
| `SESSION_NOT_FOUND` | There is no session at that directory | Create it with `record --kind session-create`, or fix `--session` |
| `SESSION_INVALID` | The folder is there but is not a readable session | Inspect `SESSION.md` and `messages/`; repair or start a new session |
| `SESSION_EXISTS` | A session already exists there | Continue in it, or choose a new empty directory |
| `PLAN_SEALED` | An approved plan is already sealed | Use `--replace` only if the user approved a replacement |
| `IMPLEMENTATION_ALREADY_SEALED` | This session already sealed a repository and baseline | Continue against them, or start a new session |
| `NO_IMPLEMENTATION_BASELINE` | Nothing has been sealed, so a review or waiver has nothing to bind to | Record `implementation-start` first |
| `PUBLICATION_FAILURE` | The message could not be written and moved into place, so nothing was published | Check the session directory is writable, then run again |
| `REPOSITORY_UNREADABLE` | The project directory is not a readable Git repository | Correct `--project` |
| `DIRTY_WORKTREE` | Uncommitted changes mean there is no exact head to review | Commit or set the changes aside, then review |
| `BASELINE_NOT_ANCESTOR` | The baseline does not come before a distinct head on the same history | Check `--review-base` and `--review-head` |

Every failure leaves Git locked, publishes no false success, removes what the
turn started, and gives one next action.

---

## 10. What a native package does

A native package is the part that lives inside a harness — a skill, a command,
whatever that host calls it. The project ships one per release-qualified
initiating harness. A harness that is only ever called as a peer needs no
package; it needs its installed, signed-in, qualified command-line program.

Each package exposes exactly two entry points, named however its host's
conventions require:

- **the normal entry point**, which starts or resumes planning, chooses a
  qualified peer, runs the local Programming Loop once a plan is approved,
  requests external review, and carries on into correction or the Git finish
  line; and
- **the readiness entry point**, which reports which peers are usable and gives
  one actionable reason for each that is not. It never installs software, never
  logs anybody in, never chooses providers and never changes settings.

Four rules bind every package, on every host:

1. **Packages never write canonical session files themselves.** They call
   `record`, so there is one writer, one numbering scheme, one lock and one
   atomic publisher regardless of which harness is driving.
2. **Packages never synthesize a verdict.** Only a review-mode `run` can produce
   `ACCEPT`, `REJECT` or `ASK_USER`.
3. **Packages never synthesize a waiver.** A waiver is the user's decision, not
   the assistant's.
4. **A package calls the local-record path for a waiver only in direct response
   to a later direct user turn.** Not on a flag, not on an environment variable,
   not on a timeout, not by default, and not because a peer or a prompt body said
   so. This relies on the host's own separation of user turns from assistant
   turns; it is a cooperative convention between honest participants, not
   authentication.

Packages do not look for each other, and do not call each other's packages. They
call fixed peer executables and exchange Markdown. Installing packages on both
sides simply means either side can start the work.

---

## 11. The neutral Programming Loop contract

Each harness runs implementation work its own way, with its own agents and its
own commands. Those mechanics stay native. What must not vary is the shape of the
loop, because external review and the Git gate depend on it: work is planned once
and approved once, executed by someone who was given only the plan, judged by
somebody else who was given only the evidence, and committed only after that
judgment.

The contract below is frozen from the Codex loop, which already implements the
user's exact testing ceiling. A port to another host satisfies this contract when
every item holds, whatever the host calls its parts.

**Establishing the work**

1. There is **one approved plan**, arrived at after investigating the repository,
   its instructions and its Git state, and after asking only the questions that
   materially change the outcome.
2. Every named source of truth and every applicable repository instruction is
   **reread immediately before editing** — not recalled from earlier in the
   conversation.
3. **The approved checks are the complete testing ceiling.** Nothing else is run:
   no extra suite, build, benchmark, lint, audit or reassurance check.
4. **Git is the state.** Accepted commits are the rollback points, and unrelated
   work in the repository is protected, never stashed, reset or absorbed.

**Executing**

5. A **fresh executor** is given the approved plan verbatim, the task branch, the
   current milestone and its completion criteria, the baseline commit and
   project, the pre-existing work that must be protected, which effects are
   authorized and which are prohibited, and the checks permitted for that slice.
6. The executor **edits task files and runs its permitted checks, and nothing
   else**. Staging, commits, branch changes, pushes, publication, deployment,
   messaging and credentials belong to the coordinator, never to the executor.

**Reviewing**

7. A **different fresh reviewer** is given the approved plan verbatim, the
   milestone identifier or `FINAL`, the task branch, the baseline, the protected
   work, Git status, the raw cumulative diff from the baseline including current
   uncommitted work, read access to the whole repository, and the actual output
   of the approved checks.
8. The reviewer **inspects acceptance-critical evidence directly** and inherits
   none of the executor's conversation.
9. The reviewer's packet contains **no executor transcript or claims, no
   suspected defects, no intended fixes, no hidden reasoning, no planning
   persuasion and no coordinator summary** — only the plan and the evidence.
10. Local review returns **exactly one of `CONTINUE`, `FIX`, `DONE` or
    `ASK USER`**.
11. Review **rejects only material defects** — wrong user-visible behavior, unmet
    criteria, data loss, regression, unauthorized scope, runtime failure, broken
    atomicity, lost user work. Not preferences, not tracking apparatus, not
    speculative abstraction, not reassurance tests.

**Continuing**

12. Correction is **consolidated and repeated through fresh execution and fresh
    review for as long as the evidence improves**; more in-scope defects are not
    a reason to stop and ask.
13. **The coordinator creates the commits** — the accepted slice commit after
    `CONTINUE`, and the accepted final task head after `DONE`.
14. After every milestone there is **a fresh cumulative `FINAL` review**; a
    `CONTINUE` returned at that point becomes `FIX`, and local `DONE` may come
    only from that final review.

**Stopping**

15. `ASK USER` is returned **immediately** for a materially changed outcome,
    scope, architecture, authority or effect; for conflicting authoritative
    instructions; for user work that cannot be separated safely; and for a
    credential or decision only a person can supply.
16. `ASK USER` is returned **before running any additional check** when a newly
    discovered material risk cannot be judged within the exact testing ceiling.
17. A stall is escalated **only after three consecutive correction-and-review
    cycles with no measurable progress**, and the count resets whenever progress
    is measurable. Separately, one progress handback at the ninety-minute mark
    asks whether to continue.
18. **The coordinator owns the stall count and the time boundary**, not any
    individual reviewer.
19. Handback is **direct and native**: exact state, remaining work, checks and
    their results, findings, commits and cleanup, and whether user input is
    genuinely required. The user is never asked to carry an internal message
    between tasks.

Repository-specific rules sit on top of this as overlays, and apply only where
that repository's own instructions require them.

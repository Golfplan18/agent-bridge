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
instructions that coordinate planning and review. Agent Bridge treats a peer's
command-line program as a trusted program running under the user's own account,
and makes no claim to stop it reading other files that account can already read,
so invoke only harnesses you trust.

Two more boundaries follow from that same account, and both are stated here
rather than papered over.

**Reviewer context.** An executor and a reviewer are separate contexts that
inherit no conversation from each other, and the protection that makes a
reviewer independent is the packet the coordinator hands it: no executor claims,
no hidden reasoning, no prior conversation, only the plan and the evidence.
Freshness is a property of how that context is built, not of the filesystem.
Every harness writes plaintext session transcripts that the same account can
read, and Agent Bridge does not prevent a reviewer that goes looking from
finding an executor's transcript. It adds no transcript deletion, no search
prevention and no isolation subsystem.

**Repository instructions.** A peer given a project root is given that project's
`AGENTS.md` or `CLAUDE.md` along with it, because that is how these harnesses
work. Agent Bridge does not prevent it and adds no suppression wrapper. Such
instructions may govern how a repository is inspected; they cannot expand the
approved plan, create user authority, permit mutation, or authorise a prohibited
external effect. This repository itself carries no repository-local agent
instruction file, so an external reviewer of Agent Bridge takes its task
instructions from the review request.

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
  the bridge starts is launched with an explicit list of arguments and no shell
  interpretation.
- **The body always arrives on standard input.** The outgoing Markdown text is
  written to the peer program's standard input. Prompt text never appears on a
  command line, in an environment variable, or in a shell string.

The last two points are one safety property stated twice: text that a peer or a
plan may have influenced never becomes part of a command.

A native package starts the bridge the same way it starts anything else — as a
fixed argument vector, with no shell and no installed console script to depend
on:

```text
python3 -m bridge <command> ...
```

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
                            technical-error|implementation-start>
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
required together — turns the turn into an external review. They name the
commits the review request refers to, and are recorded in the response as
provenance; they condition nothing. `--timeout` is one deadline covering the
whole turn: prechecks, the peer call and capturing the response. It defaults to
900 seconds. Cleanup afterwards is bounded separately from that deadline, and
has to be: by the time cleanup matters the deadline has usually already run out,
and a budget of nothing is no way to decide how long to wait for a process to
die. There is no retry.

A turn that is stopped rather than finished cleans up the same way. An interrupt
from the keyboard, a termination signal and a hangup signal all take the same
route out: the process group the turn owns is emptied, and the session lock is
released. After a termination or a hangup the command then says in one plain
sentence that it was stopped, and exits nonzero. Two moments inside a turn are
treated differently, because raising there would do the opposite of what the
person pressing the key wants: while the child is being created, when a program
exists that nothing has yet taken responsibility for, and during the cleanup
itself, when a second signal would abandon the emptying half done. In both, the
stop waits until the moment has passed and is then raised. It changes when the
stop is raised, never whether.

Four things stay outside anybody's control and are not pretended about: being
killed outright with `SIGKILL`; a machine that loses power; a child that
deliberately puts itself into a session of its own and so leaves the group the
turn owns; and a turn run somewhere other than the main thread, where no handler
can be installed at all, so a termination signal does whatever the surrounding
program already arranged — by default, ending it at once and leaving the peer
running. The turn still goes ahead off the main thread, because refusing to work
would be worse, but the tidy exit is not available there and is not claimed. The
third of the four has an exact consequence for connectors: a harness
command-line program that daemonizes during a turn puts its work beyond this
cleanup, and must therefore fail qualification.

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

It accepts exactly five kinds and nothing else. Every kind reads its substantive
Markdown body from standard input; input that is empty or only whitespace is a
usage error.

| Kind | Required fields | Optional | What it writes |
|---|---|---|---|
| `session-create` | `--local <harness-id>`, `--peer <harness-id>`, `--workflow <planning\|programming-loop\|external-review>` | `--project <dir>` | `SESSION.md` only; no message number is allocated |
| `user-correction` | none | none | one numbered local record |
| `plan-approval` | none | `--replace` | one numbered local record holding the approved plan text, then `PLAN.md` with the same text |
| `technical-error` | none | none | one numbered local record |
| `implementation-start` | `--project <dir>`, `--baseline <commit>` | none | one numbered local record carrying the repository path and the baseline commit it was given |

One kind has an extra rule. `plan-approval` seals `PLAN.md`; sealing over an
already approved plan requires `--replace`, and the earlier approved text stays
readable in its own numbered message.

What `record` can never do is as important as what it does:

- it never invokes a peer harness;
- it never writes a `Review-Request`, `Review-Base` or `Review-Head` field;
- it accepts no kind outside the five above.

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

A request carries nothing but who it is from and who it is for, whether or not
the turn is a review. No request ever carries `Review-Request`, `Review-Base` or
`Review-Head`: those three belong to an answer - they say which request it
answers and which commits that request named - and at the moment a request is
written no answer exists.

An external-review response, which adds only facts the runner already held
before it made the call, recorded as provenance:

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

Two of the five record kinds add a runner-owned header line of their own,
placed directly after `From:`:

| Kind | Added header lines |
|---|---|
| `implementation-start` | `Repository-Path:`, `Baseline:` |
| `plan-approval` | `Plan: SEALED` or `Plan: REPLACED` |

**The body grants nothing.** Everything after the `## Body` heading is copied
through unchanged. Text under that heading cannot change routing, cannot grant
authority, cannot approve anything, and cannot name a project or a user, no
matter how convincingly it is shaped. A peer may report findings; it cannot
grant itself permission. If a review body contains a line that looks like a
header, it stays what it is: a line of prose in somebody's review. Header lines
are read only from the block above the body, so no amount of header-shaped prose
below it is ever a header.

Nothing under the `## Body` heading is read by anything anywhere in Agent Bridge.

---

## 8. The internal failure list

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
| `RESTRICTIONS_UNAVAILABLE` | The harness lacks the switches needed to make the peer unable to write project files, alter Git state, or cause a prohibited external effect — by removing the tools, by an enforced sandbox, or by both | Do not give it real project access; report the missing restriction |
| `QUALIFICATION_UNSAFE_OR_INCONCLUSIVE` | The disposable probe did not clearly prove the boundary held | Read the reported synthetic path, work out what happened, requalify |
| `BUSY_SESSION` | Another turn holds this session's lock | Wait for it to finish and run again; nothing was changed |
| `TIMEOUT` | The deadline passed before an answer arrived | Run again with a longer `--timeout`, or check the peer by hand |
| `PEER_FAILURE` | The peer's program exited with a failure | Read its own error output and fix it inside that harness |
| `EMPTY_RESPONSE` | The peer produced no text at all | Check the peer by hand and run again |
| `CLEANUP_FAILURE` | Something the turn created could not be removed | Remove the reported path or process by hand and confirm nothing is left |
| `USAGE_ERROR` | Missing, conflicting or empty arguments, including empty input | Correct the command line and the input, then run again |
| `UNKNOWN_HARNESS` | The named harness is not one of the five | Name one of the five identifiers |
| `CONNECTOR_UNAVAILABLE` | A real identifier, but no connector for it ships in this build | Use a harness whose connector ships |
| `UNKNOWN_RECORD_KIND` | Not one of the five record kinds | Name one of the five kinds |
| `SESSION_NOT_FOUND` | There is no session at that directory | Create it with `record --kind session-create`, or fix `--session` |
| `SESSION_INVALID` | The folder is there but is not a readable session | Inspect `SESSION.md` and `messages/`; repair or start a new session |
| `SESSION_EXISTS` | A session already exists there | Continue in it, or choose a new empty directory |
| `PLAN_SEALED` | An approved plan is already sealed | Use `--replace` only if the user approved a replacement |
| `PUBLICATION_FAILURE` | The message could not be written and moved into place, so nothing was published | Check the session directory is writable, then run again |
| `PUBLICATION_NOT_FLUSHED` | The message is written and in place, but the folder entry could not be forced to disk, so a machine failure could lose it | Confirm the reported file is there, and treat the turn as unfinished until the disk is behaving |
| `PUBLICATION_UNCERTAIN` | Something went wrong while the message was being moved into place, and the canonical name could not then be examined, so there is no telling whether the message reached it | Look in the session's messages folder for the reported file before anything else — there means complete, absent means never arrived — and do not run the command again until you know which |

Every failure publishes no false success, removes what the turn started, and
gives one next action.

---

## 9. What a native package does

A native package is the part that lives inside a harness — a skill, a command,
whatever that host calls it. The project ships one per release-qualified
initiating harness. A harness that is only ever called as a peer needs no
package; it needs its installed, signed-in, qualified command-line program.

Each package exposes exactly two entry points, named however its host's
conventions require:

- **the normal entry point**, which starts or resumes planning, chooses a
  qualified peer, runs the local Programming Loop once a plan is approved, and
  requests external review — the findings come back to the user, who decides;
  and
- **the readiness entry point**, which reports which peers are usable and gives
  one actionable reason for each that is not. It never installs software, never
  logs anybody in, never chooses providers and never changes settings.

One rule binds every package, on every host: **packages never write canonical
session files themselves.** They call `record`, so there is one writer, one
numbering scheme, one lock and one atomic publisher regardless of which harness
is driving.

Packages do not look for each other, and do not call each other's packages. They
call fixed peer executables and exchange Markdown. Installing packages on both
sides simply means either side can start the work.

---

## 10. The neutral Programming Loop contract

Each harness runs implementation work its own way, with its own agents and its
own commands. Those mechanics stay native. What must not vary is the shape of the
loop, because external review depends on it: work is planned once and approved
once, executed by someone who was given only the plan, judged by somebody else
who was given only the evidence, and committed only after that judgment.

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

**What "fresh" and "different" mean here**, since the three items above turn on
it. They mean a separately created executor or reviewer context that inherits no
conversation and is given only the coordinator's packet. Freshness is a property
of how that context is built, not of the filesystem. Every harness writes
plaintext session transcripts readable by the same account, and Agent Bridge
does not prevent a reviewer that goes looking from finding an executor's
transcript; the protection is item 9 — the coordinator does not hand over
executor claims, hidden reasoning or prior conversation. No transcript deletion,
search prevention or isolation subsystem exists, and none is claimed. A role
prompt inside one inherited conversation does not satisfy this.
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

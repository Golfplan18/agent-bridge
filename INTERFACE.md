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
and capturing the response. It defaults to 900 seconds. Cleanup afterwards is
bounded separately from that deadline, and has to be: by the time cleanup
matters the deadline has usually already run out, and a budget of nothing is no
way to decide how long to wait for a process to die. There is no retry.

A turn that is stopped rather than finished cleans up the same way. An interrupt
from the keyboard, a termination signal and a hangup signal all take the same
route out: the process group the turn owns is emptied, the review evidence is
deleted, and the session lock is released. After a termination or a hangup the
command then says in one plain sentence that it was stopped, and exits nonzero.
Two moments inside a turn are treated differently, because raising there would
do the opposite of what the person pressing the key wants: while the child is
being created, when a program exists that nothing has yet taken responsibility
for, and during the cleanup itself, when a second signal would abandon the
emptying half done. In both, the stop waits until the moment has passed and is
then raised. It changes when the stop is raised, never whether.

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

A review request, which adds one runner-owned line naming the exact file the
runner generated for this turn. That line is the runner's own note of what it
made, kept so that whoever reads the session afterwards can see what the
reviewer was pointed at. It is not what gets the evidence to the peer and the
peer never sees it: a header sits above the body, and the peer receives only the
body. What tells a peer where to read is the connector's restriction switches,
and what shows it read is described in section 8:

```markdown
# Message 0011
From: codex
To: claude
Review-Evidence: /tmp/agent-bridge-review-evidence-xxxxxxxx.diff

## Body

<the review instruction copied verbatim>
```

An ordinary request never carries that line. No request of either kind ever
carries `Review-Request`, `Review-Base` or `Review-Head`: those three bind an
answer, and at the moment a request is written no answer exists.

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
reviewed, so `run` binds it to commits. As little as possible of what a
repository says about itself is allowed to decide what the gate sees or to make
it start a program, so every Git command the gate runs carries the same fixed
overrides — this list, exactly, and one thing it does not cover is named
immediately after it:

- **replacement objects off**, because Git lets a repository say "wherever you
  see this commit, read that one instead", and a review that honoured such a
  mapping would show a reviewer contents the `Review-Head` line does not name;
- **external difference programs and text-conversion filters off**, for the same
  reason — what a reviewer reads is Git's own output for two named commits;
- **the filesystem-monitor helper off**, because `core.fsmonitor` names a program
  of the repository's choosing that Git starts while reading a worktree, and a
  supposedly read-only check that runs somebody else's program has already had an
  effect before the peer was even started;
- **the hook directory pointed at nothing**, so no hook can be found however Git
  changes; and
- **automatic housekeeping off**, so reading a repository never begins work in it.

**What that list does not cover.** A repository can name a content filter —
`filter=<name>` in its own `.gitattributes`, with `filter.<name>.clean` in its
local configuration — and Git runs that program while working out whether the
worktree is clean. Agent Bridge does not switch that off, and says so rather
than implying otherwise: filters are named one at a time and cannot be turned
off as a class, and the one mechanism that would suppress them needs a newer Git
than this must run on, a per-repository lookup, and would change what the
evidence file itself shows. The exposure needs the local `.git/config` of the
repository under review to define such a filter — under the same-user trust
boundary, the user's own configuration. Do not point a review at a repository
whose local configuration you did not write.

A worktree counts as clean only when Git reports nothing at all — no uncommitted
change, no untracked file, and no ignored file either, since being ignored by Git
says nothing about whether a reviewing peer can read something no commit
contains. Untracked files are asked for **by name on the command line**, not left
to the repository's preference: a repository may set `status.showUntrackedFiles`
to `no`, and then an ordinary untracked file — and every ignored one with it —
simply does not appear in the answer, so a worktree that hides what it holds
would pass as clean. Ignored files are reported, never deleted: they are the
user's own files.

The reviewing peer is given the project and one file to read, and no shell and
no Git. That file is the `baseline..head` diff, generated once, with a fixed
argument vector and the overrides above, into one temporary file the runner
owns, outside both the project and the session record — followed by one more
line, described next.

How the runner and the connector fit together at that moment is fixed, because
one depends on the other. The runner is not handed a ready-made command. It
writes the evidence first, and then calls the connector to compose the fixed
argument vector for this one call, giving it the exact path of that evidence file
— `None` when the turn is not a review — and this turn's deadline. The connector
names that exact path in the restrictions it applies, so the peer is told
precisely where the evidence is and the runner can afterwards check that what
was granted is what it wrote. Every version, authentication and restriction
subprocess the connector runs to answer that call goes through the shared
bounded process runner with that same deadline; there is no second way to start
a program and no separate budget for one.

**The evidence ends with one line nobody but this turn could have written.** A
real difference contains nothing unpredictable — a peer could describe it from
the change itself — so the runner appends a token made from fresh random bytes
for that turn alone:

```text
Agent-Bridge-Evidence-Token: <32 hexadecimal characters>
```

The runner then appends its own instruction to the outgoing body, telling the
peer to open the evidence file and copy that line back. The instruction names
the beginning of the line and never its value, so the only way to produce the
value is to read the file. **No answer becomes an acceptance unless the value
comes back**, and the value appearing anywhere in the response is enough: what
is being shown is that the file reached the peer and could be read, not that a
model weighed every line of it — no check could show the second thing, and
saying otherwise would be worse than saying which one this is.

A well-formed verdict with the token missing is treated exactly as a response
whose last line is not a verdict. The peer's prose is published as an **ordinary
message** carrying none of the `Review-*` fields, so it holds no authority; the
call fails with `REVIEW_EVIDENCE_NOT_DELIVERED`; Git stays locked; nothing is
rewritten or retried; and acceptance requires a fresh review call. Useful
findings are not thrown away for a missing token, and a missing token is not
forgiven either.

The connector also states, as part of the command it returns, what it granted the
peer read access to: the project root, and the review-evidence file. Neither has
a default. The runner resolves both statements and both of its own paths to real
paths and requires exact agreement — a review must name a real evidence file, an
ordinary turn must name none, and a turn with no project must be granted none.
It also records a digest of the exact bytes it wrote as evidence and checks the
file again the moment the peer finishes.

A reviewer therefore cannot be merely trusted to have read the right thing. A
mismatched grant stops the turn before the peer is started; a file that was
replaced, truncated or removed voids the turn afterwards; and an answer that
never quotes the token opens nothing.

**What those three checks do not catch**, stated rather than implied away:

- Paths are compared with symbolic links resolved but letter case unfolded. On a
  filesystem that ignores case — the ordinary macOS one — two spellings that
  differ only in case name one file and are called different here, so the turn is
  refused. That is the safe way round, and a connector that hands back the path it
  was given never meets it.
- A symbolic link is resolved once, before the peer starts. A link changed after
  that points somewhere else and nothing looks again. Under the same-user trust
  boundary that is not a defence this could offer anyway: whoever could move the
  link could read the file.
- The digest compares two moments, not the time between them. Evidence replaced
  with identical bytes passes, which is the intended answer — the file still
  holds the difference this turn generated. Evidence that was something else for
  a while and was put back before the peer finished passes too, and that one is a
  genuine gap.

One review turn therefore runs in this order, holding the session lock:

1. the before-review repository checks — resolve both commits, require the review
   baseline to equal the baseline sealed at `implementation-start`, require it to
   be an ancestor of a distinct task head, confirm the sealed repository, a clean
   worktree and the exact expected `HEAD`;
2. generate the evidence — the difference followed by this turn's token — and
   record its exact bytes and that token;
3. check the deadline, compose the command, check the deadline again;
4. require the connector's declared project and evidence paths to match exactly;
5. publish the request: the outgoing body with the runner's instruction to copy
   the token appended, and a `Review-Evidence:` line the runner owns naming the
   file it generated;
6. run the peer inside the deadline;
7. confirm the evidence is still exactly the bytes that were written;
8. read the verdict, and require the token to appear in the response;
9. delete the evidence — which happens on the way out of any of steps 2 to 8,
   whatever their outcome, and not only when they all succeeded;
10. repeat the repository, cleanliness and `HEAD` checks;
11. publish the response, carrying the binding fields only when the verdict is
    valid and the token came back.

A failure before step 5 publishes no request, because nothing was sent. Any
mismatch at any step, or any failure to clean up, voids the verdict as a
technical error.

Git unlocks only when all six of these hold at once:

1. the call succeeded — the peer was started, answered, and exited normally;
2. the verdict is exactly `ACCEPT`;
3. the response contains the evidence token this turn generated;
4. the response is bound to its request, and to the baseline and head sealed at
   `implementation-start`;
5. both rounds of repository checks passed, before the call and after it; and
6. `HEAD` is still the head that was reviewed.

A new commit invalidates the verdict.

**What gets written down when the answer is not an acceptance** matters as much
as when it is. `REJECT` and `ASK_USER` are published exactly like any other
answer — they are decisions a reviewer really made — and they unlock nothing.

The rule for failures is that **no failed call ever publishes an authoritative
verdict.** Where the call did not finish cleanly, nothing is published at all: an
empty response, a peer that failed or ran out of time, a changed repository,
baseline or head, a dirty worktree, or a failure to clean up. Captured text in
those cases may be a fragment of an answer the peer never finished, and a
fragment must not stand in for a reply.

Two failures keep the text, and they are the same rule twice: `INVALID_VERDICT`,
where a peer exited cleanly with real output and got only its final line wrong,
and `REVIEW_EVIDENCE_NOT_DELIVERED`, where it answered without the evidence
token. Both have that output published as an **ordinary message** — the same
shape any non-review answer takes, carrying none of the `Review-*` fields that
bind an answer to a request and to two commits. It therefore holds no
external-review authority and unlocks nothing. The repository checks at step 10
come first, whatever the answer looked like, because prose is only worth keeping
once the code it describes is known to be still there: a repository that moved
during the review reports the movement and publishes nothing at all. The call
still fails, Git stays locked, and the text is never rewritten, read for an
intention, or retried; acceptance requires a fresh review call. A reviewer that
did good work and fumbled one line, or forgot to quote one, should not lose the
work.

In every failing case the request message that was already published stays where
it is, because it truthfully records what was sent, and the workflow writes the
failure down itself with `record --kind technical-error`.

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
| `RESTRICTIONS_UNAVAILABLE` | The harness lacks the switches needed to deny project writes and to take away its shell, Git and network access | Do not give it real project access; report the missing restriction |
| `QUALIFICATION_UNSAFE_OR_INCONCLUSIVE` | The disposable probe did not clearly prove the boundary held | Read the reported synthetic path, work out what happened, requalify |
| `BUSY_SESSION` | Another turn holds this session's lock | Wait for it to finish and run again; nothing was changed |
| `TIMEOUT` | The deadline passed before an answer arrived | Run again with a longer `--timeout`, or check the peer by hand |
| `PEER_FAILURE` | The peer's program exited with a failure | Read its own error output and fix it inside that harness |
| `EMPTY_RESPONSE` | The peer produced no text at all | Check the peer by hand and run again; Git stays locked |
| `INVALID_VERDICT` | A review response did not end with one of the three exact lines; the text was kept as an ordinary message with no review authority | Read the kept message if useful, then run the review again; this is never an acceptance |
| `REPOSITORY_CHANGED` | The repository is not the one sealed at implementation start | Point at the sealed repository, or start a new session |
| `BASELINE_CHANGED` | The review baseline is not the sealed baseline | Run the review again with the sealed baseline |
| `HEAD_CHANGED` | The branch moved, so the review no longer describes the code | Run a fresh review against the current head |
| `REVIEW_EVIDENCE_UNAVAILABLE` | The file holding the difference could not be created or written, and what had been written was removed | Free space on the temporary filesystem, or point `TMPDIR` somewhere writable |
| `REVIEW_EVIDENCE_NOT_DELIVERED` | The peer was granted a different file from the one written, or that file changed while the peer had it, or the peer answered without the token that appears only inside it; where it answered, its text was kept as an ordinary message with no review authority | Make the connector grant exactly the paths it was handed, make sure the peer reads that file and copies its token line back, check nothing else writes there |
| `CLEANUP_FAILURE` | Something the turn created could not be removed — including a partly written evidence file whose removal failed after the write did, which is reported as the cleanup failure it is rather than only as unavailable evidence | Remove the reported path or process by hand and confirm nothing is left |
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
| `PUBLICATION_NOT_FLUSHED` | The message is written and in place, but the folder entry could not be forced to disk, so a machine failure could lose it | Confirm the reported file is there, and treat the turn as unfinished until the disk is behaving |
| `REPOSITORY_UNREADABLE` | The project directory is not a readable Git repository | Correct `--project` |
| `DIRTY_WORKTREE` | Something no commit contains is in the worktree — an uncommitted change, an untracked file, or an ignored file — so there is no exact head to review | Commit or set the changes aside, and move the ignored files out yourself; Agent Bridge never deletes one |
| `BASELINE_NOT_ANCESTOR` | The baseline does not come before a distinct head on the same history | Check `--review-base` and `--review-head` |

Every failure leaves Git locked, publishes no false success, removes what the
turn started, and gives one next action. `INVALID_VERDICT` and
`REVIEW_EVIDENCE_NOT_DELIVERED` are the two that keep the peer's text, and only
when the peer really answered — as an ordinary message that carries no review
authority.

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

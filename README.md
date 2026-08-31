# Agent Bridge

Agent Bridge lets two coding-agent harnesses work on one job together. It calls
them through the official command-line programs their vendors publish. It does
not connect model APIs.

What the bridge owns is deliberately small: one bounded call to a peer harness,
an ordered exchange of Markdown files on disk, an explicit least-authority
invocation, one lock per session, publication that either completes or does not
happen at all, cleanup of everything the turn started, and the written
instructions that coordinate planning and review. Agent Bridge treats a peer's
command-line program as a trusted program running under the user's own account,
and makes no claim to stop it reading other files that account can already read,
so invoke only harnesses you trust.

Two further boundaries follow from that same account, and both are stated rather
than papered over.

**Reviewer context.** An executor and a reviewer are separate contexts that
inherit no conversation from each other, and what makes a reviewer independent is
the packet it is handed: the plan and the evidence, never the executor's claims,
hidden reasoning or prior conversation. That is a property of how the context is
built, not of the disk. Every harness writes plaintext session transcripts the
same account can read, and Agent Bridge does not stop a reviewer that goes
looking from finding one. It adds no transcript deletion, no search prevention
and no isolation subsystem.

**Repository instructions.** A peer given a project root is given that project's
`AGENTS.md` or `CLAUDE.md` with it. Agent Bridge does not prevent that and adds
no suppression wrapper. Such instructions may govern how a repository is
inspected; they cannot expand the approved plan, create user authority, permit
mutation, or authorise a prohibited external effect. This repository carries no
agent instruction file of its own, so an external reviewer of Agent Bridge takes
its instructions from the review request.

Everything else stays where it already lives. Each harness keeps its own
authentication, subscription, providers, models, tools, agents and native
sessions, and does its own implementation work its own way. The project
repository and Git remain the record of what was built and the way to undo it.
There is no daemon, no scheduler and no background service; when the foreground
command exits, Agent Bridge is idle.

## Status

This repository is under construction and has not been released. No harness is
release-qualified, nothing here is supported, and there is no installation path
yet.

## Interface

`INTERFACE.md` is the frozen interface: the harness identifiers, the commands,
the session record and message formats, the verdict rules, the internal failure
list, what a native package must do, and the neutral Programming Loop contract.

## License

SPDX-License-Identifier: Unlicense

This is free and unencumbered software released into the public domain. See
`UNLICENSE`.

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

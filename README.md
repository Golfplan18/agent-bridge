# Agent Bridge

Agent Bridge is a standalone one-to-many Markdown courier. Any application or
coding-agent harness can use one shared Bridge installation to make a bounded
call to Codex, Claude Code, ZCode, or Hermes Agent through the official
command-line program that vendor publishes. Agent Bridge does not connect model
APIs.

What the bridge owns is deliberately small: target readiness, one request and
one response, an ordered Markdown record, an explicit least-authority
invocation, one lock per session, atomic publication, a bounded foreground
process, and cleanup of everything the turn started.

Applications own everything else. They may plan, review, coordinate several
targets, manage Git, or interpret responses, but none of that behavior is part
of Agent Bridge. Initiators identify themselves with an inert label; adding
another application requires no Bridge registry, connector, or release.

Agent Bridge treats a target CLI as a trusted program running under the user's
own account. It makes no claim to stop that program reading other files the
account can already read, so users should invoke only harnesses they trust.
Every connector must still prove that its production restrictions prevent
project mutation, Git changes, and prohibited external effects.

A target given a project may load that project's `AGENTS.md`, `CLAUDE.md`,
or equivalent instructions. Target CLIs may also write their own plaintext
transcripts. Agent Bridge neither suppresses repository instructions nor hides
vendor transcripts.

Each harness keeps its own authentication, subscription, providers, models,
tools, agents, and native sessions. Agent Bridge installs nothing, signs in to
nothing, selects no model or provider, and has no API fallback. There is no
daemon, scheduler, database, coordinator, router, workflow engine, Git gate, or
background service; when the foreground command exits, Agent Bridge is idle.

## Status

This repository is under construction and has not been released. The frozen
courier interface is the Release 1 target; current code may not yet conform to
all of it. No harness is release-qualified, nothing here is supported, and
there is no installation path yet.

## Interface

`INTERFACE.md` defines the frozen courier contract: initiator labels, the four
callable targets, commands, session and message formats, connector
qualification, failures, cleanup, thin adapter responsibilities, and Release 1
transport conformance.

It intentionally contains no planning, Programming Loop, external-review, Git,
approval, or application-workflow contract.

## License

SPDX-License-Identifier: Unlicense

This is free and unencumbered software released into the public domain. See
`UNLICENSE`.

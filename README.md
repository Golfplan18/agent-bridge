# Agent Bridge

Agent Bridge is a standalone one-to-many Markdown courier. Any application or
coding-agent harness can use one shared Bridge installation to make a bounded
call to Codex, Claude Code, ZCode, Hermes Agent, MiniMax Code, or Qwen Code
through the official command-line program that vendor publishes. Agent Bridge
does not connect model APIs.

What the bridge owns is deliberately small: target readiness, one request and
one response, an ordered Markdown record, an explicit least-authority
invocation, clear warnings about its remaining limits, one lock per session,
atomic publication, a bounded foreground process, and cleanup of everything
the turn started.

Applications own everything else. They may plan, review, coordinate several
targets, manage Git, or interpret responses, but none of that behavior is part
of Agent Bridge. Initiators identify themselves with an inert label; adding
another application requires no Bridge registry, connector, or release.

Agent Bridge treats a target CLI as a trusted program running under the user's
own account. It makes no claim to stop that program reading other files the
account can already read, so users should invoke only harnesses they trust.
Every connector applies the strongest practical vendor safeguards. Where those
safeguards cannot guarantee complete confinement, Bridge says so during
`check` and immediately before a warned `run` publishes its request, then
proceeds without an acknowledgment prompt, approval switch, or stored consent.
An untested version may proceed with a warning when its required switches and
one-shot transport still work.

Bridge records and passes the original Markdown exactly. Qwen Code 0.23.0 alone
may preprocess recognized leading `/` commands or unescaped `@` references,
altering the prompt, appending readable content, failing before a model call,
or handling a command itself. Both headless modes share this; safe mode cannot
disable it and no raw switch exists. Qwen must run with `--max-tool-calls=0`:
no model-initiated tool call can execute, and the first such attempt aborts the
run. Input preprocessing happens before that budget, so the limit does not stop
it. Bridge warns without blocking during `check` and before publication; the
other five prompts remain lossless.

A target given a project may load that project's `AGENTS.md`, `CLAUDE.md`,
or equivalent instructions. Target CLIs may also write their own plaintext
transcripts. Agent Bridge neither suppresses repository instructions nor hides
vendor transcripts.

Each harness keeps its own authentication, subscription, providers, models,
tools, agents, and native sessions. Agent Bridge installs nothing, signs in to
nothing, selects no model or provider, and has no API fallback. There is no
daemon, scheduler, database, coordinator, router, workflow engine, Git gate, or
background service; when the foreground command exits, Agent Bridge is idle.

The six target identifiers are literal: `codex`, `claude`, `zcode`, `hermes`,
`minimax`, and `qwen`. Only the selected connector is imported or examined;
every other vendor remains inert, with no probe, process, project access,
login, network call, or fallback. Codex, Claude, and ZCode are project-capable.
Hermes, MiniMax, and Qwen are courier-only and receive no project directory.

Missing software or minimum authentication, unusable input or final output,
missing required command mechanics, and inability to control the foreground
process remain honest failures because Bridge cannot make a truthful call in
those conditions.

## Status

This repository is under construction and has not been released. The revised
six-target courier interface is the Release 1 target; current code may not yet
conform to all of it. No harness is release-qualified, nothing here is
supported, and there is no installation path yet.

## Interface

`INTERFACE.md` defines the courier contract: initiator labels, six callable
targets, selected-only activity, commands, session and message formats,
warnings, safe qualification, failures, cleanup, six thin adapter
responsibilities, exact checks, and the public-release finish line.

It intentionally contains no planning, Programming Loop, external-review, Git,
approval, or application-workflow contract.

## License

SPDX-License-Identifier: Unlicense

This is free and unencumbered software released into the public domain. See
`UNLICENSE`.

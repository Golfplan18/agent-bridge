"""The hand-written Qwen Code courier connector.

Qwen Code 0.23.0 supplies a safe mode, explicit plan posture, run budgets,
zero-model-tool-call guard, standard-input transport, and buffered JSON output.
The connector keeps the user's normal QWEN_HOME so authentication is not
hidden, while putting transient output inside the task-owned neutral directory
and disabling compatible usage statistics and telemetry for this child. Qwen
alone may preprocess the unchanged body before the model; that exception is
reported explicitly rather than hidden behind the later zero-tool-call budget.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import json
import math
import os
from typing import List, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

HARNESS_ID = "qwen"
COURIER_ONLY = True

QUALIFICATION = connectors.Qualification(
    cli_identity="qwen",
    versions=("0.23.0",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--safe-mode",
        "--sandbox",
        "--chat-recording",
        "--approval-mode",
        "--disabled-slash-commands",
        "--max-tool-calls",
        "--max-session-turns",
        "--max-wall-time",
        "--input-format",
        "--output-format",
        "--openai-logging",
    ),
)

RUNTIME_DIRECTORY = ".qwen-runtime"
STDIN_BODY_LIMIT = 8 * 1024 * 1024
MAX_NATIVE_WALL_TIME_SECONDS = 2_147_483
CONTROLLED_ENVIRONMENT = (
    "QWEN_RUNTIME_DIR",
    "QWEN_TELEMETRY_ENABLED",
    "QWEN_USAGE_STATISTICS_ENABLED",
    "NODE_DISABLE_COMPILE_CACHE",
    "NO_BROWSER",
    "QWEN_SANDBOX",
    "SANDBOX",
    "SEATBELT_PROFILE",
    "QWEN_SANDBOX_PROXY_COMMAND",
)

INPUT_WARNING = (
    "Qwen Code 0.23.0 may preprocess enabled recognized leading / commands and "
    "unescaped @ references before the model in both text and stream-json "
    "headless input. It may alter or replace the effective prompt, inject "
    "readable file or resource content, fail during preprocessing, or "
    "complete a command without a model call. Agent Bridge disables the "
    "pre-model command families that can report externally, persist or import "
    "user configuration, update the CLI, write diagnostics, or invoke "
    "installer rollback: /bug, /config, /update, /import-config, /language, /effort, "
    "/model, and /doctor. Other recognized slash-command preprocessing "
    "remains enabled. Safe mode does not disable it and no lossless raw "
    "escape or switch exists. Agent Bridge "
    "records and passes the original request unchanged, but preprocessing "
    "happens before --max-tool-calls=0, so that budget does not stop it."
)

BOUNDARY_WARNING = (
    "Qwen Code is courier-only and receives a task-owned neutral directory. "
    "--safe-mode drops ambient context, hooks, extensions, MCP servers, custom "
    "subagents, permission rules, and memory features. Agent Bridge removes "
    "ambient sandbox selectors and the Qwen sandbox proxy command, forces the "
    "native macOS restrictive-open sandbox profile, and disables browser "
    "launches. That profile still permits same-user reads, process launches, "
    "writes to Qwen, cache, temporary, and task-owned paths, and outbound "
    "network access; Qwen's bundled skills still load and can shape the turn. "
    "Plan mode plus "
    "--max-tool-calls=0 prevents model-initiated tool execution and aborts the "
    "run on the first such attempt. "
    "QWEN_HOME remains visible so the user's existing authentication and "
    "provider selection can work, and live authentication cannot be confirmed "
    "without the bounded model call. The configured model-provider connection "
    "and same-user CLI read access remain outside Agent Bridge confinement."
)


def _environment(cwd: str) -> Tuple[Tuple[str, str], ...]:
    """Keep authentication, but make the native sandbox choice deterministic."""
    inherited = [
        (name, value)
        for name, value in connectors.environment()
        if name not in CONTROLLED_ENVIRONMENT
    ]
    inherited.extend(
        (
            ("QWEN_RUNTIME_DIR", os.path.join(cwd, RUNTIME_DIRECTORY)),
            ("QWEN_TELEMETRY_ENABLED", "0"),
            ("QWEN_USAGE_STATISTICS_ENABLED", "0"),
            ("NODE_DISABLE_COMPILE_CACHE", "1"),
            ("NO_BROWSER", "1"),
            ("SEATBELT_PROFILE", "restrictive-open"),
        )
    )
    return tuple(inherited)


def parse_response(output: str) -> str:
    """Extract the last successful result from Qwen's buffered JSON array."""
    try:
        messages = json.loads(output)
    except ValueError as exc:
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen returned no readable JSON array: {0}".format(exc),
        )
    if not isinstance(messages, list) or not messages:
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's JSON output was not a nonempty message array",
        )
    result = messages[-1]
    if not isinstance(result, dict) or result.get("type") != "result":
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's final JSON message was not a result",
        )
    if result.get("subtype") != "success" or result.get("is_error") is not False:
        error = result.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("type")
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's final JSON result reported failure: {0}".format(
                # The runner removes echoed requests and credentials before
                # shortening this diagnostic; partial echoes cannot be matched.
                str(error or result.get("subtype") or "unknown")
            ),
        )
    text = result.get("result")
    if not isinstance(text, str):
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's successful JSON result contained no text",
        )
    return text


def _prerequisites(
    deadline: Deadline, cwd: str
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    warnings = []  # type: List[str]
    program = connectors.executable(QUALIFICATION.cli_identity)
    environment = _environment(cwd)
    version = connectors.qualified_version(
        connectors.probe(
            (program, "--version"), cwd, deadline, environment
        ).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)
    connectors.qualified_restrictions(
        connectors.probe((program, "--help"), cwd, deadline, environment),
        QUALIFICATION,
    )
    warnings.extend((INPUT_WARNING, BOUNDARY_WARNING))
    return (
        program,
        version,
        described,
        "no safe noninteractive authentication-status command is available",
        tuple(warnings),
    )


def check(deadline: Deadline, cwd: str) -> connectors.CheckResult:
    program, version, described, account, warnings = _prerequisites(deadline, cwd)
    return connectors.readiness(
        HARNESS_ID,
        program,
        version,
        described,
        account,
        warnings,
        authentication_confirmed=False,
    )


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    program, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd
    )
    # Qwen's timer starts only after this process starts. Giving it the
    # original public duration, rounded up to the integral duration its CLI
    # accepts, means Bridge's already-running deadline always remains the
    # authoritative one.
    native_wall_time = (
        (
            "--max-wall-time={0}s".format(
                max(1, int(math.ceil(deadline.seconds)))
            ),
        )
        if deadline.seconds <= MAX_NATIVE_WALL_TIME_SECONDS
        else ()
    )
    return connectors.PeerCommand(
        argv=(
            program,
            "--safe-mode",
            "--sandbox=sandbox-exec",
            "--chat-recording=false",
            "--approval-mode=plan",
            "--disabled-slash-commands="
            "bug,config,update,import-config,language,effort,model,doctor",
            "--max-tool-calls=0",
            "--max-session-turns=1",
        )
        + native_wall_time
        + (
            "--input-format=text",
            "--output-format=json",
            "--openai-logging=false",
        ),
        cwd=cwd,
        env=_environment(cwd),
        warnings=warnings,
        response_parser=parse_response,
        stdin_body_limit=STDIN_BODY_LIMIT,
    )

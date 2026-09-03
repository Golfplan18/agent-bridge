"""The hand-written MiniMax Code courier connector.

MiniMax Code 0.2.7 has a stable headless ``mcode exec`` transport, but no
headless permission mode that confines its tools. Agent Bridge therefore gives
it only a task-owned neutral directory and reports the remaining tool and
configuration authority instead of presenting ``smart`` as a sandbox.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import math
from typing import List, Tuple

from . import connectors
from .peer import Deadline

HARNESS_ID = "minimax"
COURIER_ONLY = True

QUALIFICATION = connectors.Qualification(
    cli_identity="mcode",
    versions=("0.2.7",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--input",
        "--input-format",
        "--cwd",
        "--permission",
        "--timeout",
        "--max-steps",
        "--output-format",
    ),
)

WARNING = (
    "MiniMax Code is courier-only and receives a task-owned neutral directory. "
    "--permission smart is a discretionary permission mode, not a sandbox: "
    "ask is interactive and rejected by mcode exec, while full bypasses and "
    "off disables permission checks, so the fixed vector uses none of those "
    "modes. --max-steps=1 limits assistant steps but does not disable tools "
    "within that step or create confinement. Smart does not categorically "
    "confine file writes, shell or Git "
    "commands, MCP tools, network access, or surviving user/provider "
    "configuration."
)

MAX_NATIVE_TIMEOUT_MILLISECONDS = 2_147_483_647


def _prerequisites(
    deadline: Deadline, cwd: str
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    warnings = []  # type: List[str]
    program = connectors.executable(QUALIFICATION.cli_identity)
    version = connectors.qualified_version(
        connectors.probe((program, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)
    connectors.qualified_restrictions(
        connectors.probe((program, "exec", "--help"), cwd, deadline),
        QUALIFICATION,
    )
    warnings.append(WARNING)
    warnings.append(
        "MiniMax has no state-free noninteractive authentication check: "
        "provider list can initialize its runtime and refresh or invalidate "
        "OAuth state, so Agent Bridge does not run it. Live authentication "
        "remains unconfirmed until the selected bounded call."
    )
    return (
        program,
        version,
        described,
        "no state-free noninteractive authentication-status command is available",
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
    # MiniMax starts this timer after Bridge's deadline. Rounding up means the
    # native timer cannot win; above Node's timer domain it is omitted so a
    # valid Bridge timeout is never rejected or shortened by the child.
    native_timeout = (
        (
            "--timeout",
            "{0}ms".format(
                max(1, int(math.ceil(deadline.seconds * 1000.0)))
            ),
        )
        if deadline.seconds
        <= MAX_NATIVE_TIMEOUT_MILLISECONDS / 1000.0
        else ()
    )
    return connectors.PeerCommand(
        argv=(
            program,
            "exec",
            "--input",
            "-",
            "--input-format",
            "text",
            "--cwd",
            cwd,
            "--permission",
            "smart",
        )
        + native_timeout
        + (
            "--max-steps",
            "1",
            "--output-format",
            "text",
        ),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
    )

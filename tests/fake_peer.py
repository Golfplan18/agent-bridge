#!/usr/bin/env python3
"""A stand-in for a real coding-agent harness.

The runner's job is to start one program, hand it a Markdown body on standard
input, wait for one answer, and decide what that answer means. None of that
needs a real harness, a real subscription, or a real model - it needs a program
that behaves in one chosen way. This is that program.

It is started exactly as a real peer is:

    [sys.executable, "<path>/fake_peer.py", "<mode>", ...]

It reads the whole of standard input first, then behaves according to its mode.
Each mode exists because some check needs it: a good answer, a good answer that
arrives slowly, an answer that ends wrongly, no answer, a crash, a program that
will not stop, and a program that leaves a child behind.

It imports nothing outside the standard library, touches nothing except the
input it is given and the output streams it is handed, and never sleeps without
a bound.

SPDX-License-Identifier: Unlicense
"""

import os
import subprocess
import sys
import time

#: Every supported mode, in the order the docstring above describes them.
MODES = (
    "accept",
    "reject",
    "ask-user",
    "plain",
    "unknown-verdict",
    "trailing-space",
    "lowercase",
    "fenced",
    "marker-early",
    "crlf-accept",
    "empty",
    "whitespace",
    "fail",
    "hang",
    "spawn-child-then-hang",
    "slow-accept",
)

VERDICT_PREFIX = "Agent-Bridge-Verdict: "

#: Long enough to outlast any real deadline, short enough that a stray copy
#: cannot survive the day. The sleep is a loop of short naps so a termination
#: signal is acted on at once.
MAX_SLEEP_SECONDS = 3600.0
NAP_SECONDS = 0.05

THIS_FILE = os.path.abspath(__file__)


def _read_all_stdin() -> str:
    """Consume the whole incoming body before doing anything else."""
    data = sys.stdin.buffer.read()
    return data.decode("utf-8", "replace")


def _emit(text: str) -> None:
    """Write exactly these bytes to standard output, unchanged."""
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _echoed(body: str) -> str:
    """The received body, ending in a newline so a line can follow it."""
    if not body:
        return ""
    if body.endswith("\n"):
        return body
    return body + "\n"


def _sleep_bounded(seconds: float) -> None:
    """Sleep in short naps up to a fixed bound, never forever."""
    deadline = time.time() + min(seconds, MAX_SLEEP_SECONDS)
    while time.time() < deadline:
        time.sleep(NAP_SECONDS)


def _spawn_grandchild() -> int:
    """Start one child that sleeps, deliberately in this process group.

    No new session and no new process group: the grandchild stays in the group
    the runner created, so terminating that group must reach it. A runner that
    kills only its direct child leaves this process behind, which is the thing
    the orphan check is looking for.
    """
    child = subprocess.Popen(
        [sys.executable, THIS_FILE, "hang"],
        stdin=subprocess.DEVNULL,
    )
    return child.pid


def _run(mode: str, extra: list) -> int:
    body = _read_all_stdin()
    echoed = _echoed(body)

    if mode == "accept":
        _emit(echoed + VERDICT_PREFIX + "ACCEPT\n")
        return 0

    if mode == "reject":
        _emit(echoed + VERDICT_PREFIX + "REJECT\n")
        return 0

    if mode == "ask-user":
        _emit(echoed + VERDICT_PREFIX + "ASK_USER\n")
        return 0

    if mode == "plain":
        _emit(echoed)
        return 0

    if mode == "unknown-verdict":
        _emit(echoed + VERDICT_PREFIX + "MAYBE\n")
        return 0

    if mode == "trailing-space":
        _emit(echoed + VERDICT_PREFIX + "ACCEPT \n")
        return 0

    if mode == "lowercase":
        _emit(echoed + "agent-bridge-verdict: accept\n")
        return 0

    if mode == "fenced":
        _emit(echoed + VERDICT_PREFIX + "ACCEPT\n" + "```\n")
        return 0

    if mode == "marker-early":
        _emit(
            echoed
            + VERDICT_PREFIX
            + "ACCEPT\n"
            + "That marker was part of the prose and decides nothing.\n"
        )
        return 0

    if mode == "crlf-accept":
        text = echoed + VERDICT_PREFIX + "ACCEPT\n\n\n\n"
        _emit(text.replace("\n", "\r\n"))
        return 0

    if mode == "empty":
        return 0

    if mode == "whitespace":
        _emit("\n   \n\n  \n")
        return 0

    if mode == "fail":
        sys.stderr.write("fake peer: deliberate failure\n")
        sys.stderr.flush()
        return 3

    if mode == "hang":
        _sleep_bounded(MAX_SLEEP_SECONDS)
        return 0

    if mode == "spawn-child-then-hang":
        _emit("GRANDCHILD {0}\n".format(_spawn_grandchild()))
        _sleep_bounded(MAX_SLEEP_SECONDS)
        return 0

    if mode == "slow-accept":
        if len(extra) != 1:
            sys.stderr.write("fake peer: slow-accept needs one seconds value\n")
            return 2
        try:
            seconds = float(extra[0])
        except ValueError:
            sys.stderr.write("fake peer: seconds must be a number\n")
            return 2
        _sleep_bounded(seconds)
        _emit(echoed + VERDICT_PREFIX + "ACCEPT\n")
        return 0

    sys.stderr.write(
        "fake peer: unknown mode {0!r}; known modes: {1}\n".format(
            mode, ", ".join(MODES)
        )
    )
    return 2


def main(argv: list) -> int:
    if len(argv) < 2:
        sys.stderr.write(
            "usage: fake_peer.py <mode> [args]; known modes: {0}\n".format(
                ", ".join(MODES)
            )
        )
        return 2
    return _run(argv[1], list(argv[2:]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))

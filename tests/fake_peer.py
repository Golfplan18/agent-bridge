#!/usr/bin/env python3
"""A stand-in for a real coding-agent harness.

The runner's job is to start one program, hand it a Markdown body on standard
input, wait for one answer, and decide what that answer means. None of that
needs a real harness, a real subscription, or a real model - it needs a program
that behaves in one chosen way. This is that program.

It is started exactly as a real peer is:

    [sys.executable, "<path>/fake_peer.py", "--evidence", "<path>", "<mode>", ...]

`--evidence` is optional and comes first. When it is given, this program opens
that file, finds the `Agent-Bridge-Evidence-Token:` line the runner wrote at the
end of it, and prints that line before anything else it has to say. That is the
whole of what makes it a peer that read the evidence, and leaving the option out
is how a check produces a peer that did not - which the runner must refuse,
whatever verdict such a peer returns.

It then reads the whole of standard input, and behaves according to its mode.
Each mode exists because some check needs it: a good answer, a good answer that
arrives slowly, an answer that ends wrongly, no answer, a crash, a program that
will not stop, and a program that leaves a child behind.

Two further modes are about the evidence file itself. One searches it for a
canary committed into the reviewed change, which proves the difference reached
the peer and not merely the runner's own appended line. One rewrites the file
behind the runner's back. Two more report their own process id and their child's
into a file the check names, so a check can watch those exact processes rather
than guessing - one putting the child in this turn's process group, and one
letting it escape into a session of its own.

It imports nothing outside the standard library, touches nothing except the
input it is given, the evidence path it is handed, the file it is told to write
its process ids into, and the output streams it is handed. It never sleeps
without a bound.

SPDX-License-Identifier: Unlicense
"""

import os
import re
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
    "slow-unknown-verdict",
    "read-evidence",
    "rewrite-evidence",
    "write-pids-then-hang",
    "detach-child-then-hang",
)

VERDICT_PREFIX = "Agent-Bridge-Verdict: "

#: The shape of the canary a check hides inside the reviewed change. It reaches
#: this program only through the difference itself, so quoting it is proof that
#: the change - not just the runner's appended line - was read.
DIFF_CANARY = re.compile(r"AGENT-BRIDGE-DIFF-CANARY-[0-9a-f]+")

#: The line the runner writes at the end of every evidence file. Quoting it back
#: is what the runner requires before any answer can become an acceptance.
RUNNER_TOKEN_LINE = re.compile(
    r"^Agent-Bridge-Evidence-Token: [0-9a-f]+$", re.MULTILINE
)

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


def _read_evidence(path: str) -> str:
    """The whole evidence file, or an empty string with a reason on stderr."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except OSError as exc:
        sys.stderr.write(
            "fake peer: cannot read the evidence: {0}\n".format(exc)
        )
        return ""


def _runner_token_line(path: str) -> str:
    """The runner's own token line out of the evidence file it wrote.

    Returned with a trailing newline so it can simply go in front of whatever
    the mode says next. An empty string means it was not there, which is a
    failure this program reports rather than papers over.
    """
    evidence = _read_evidence(path)
    if not evidence:
        return ""
    found = RUNNER_TOKEN_LINE.search(evidence)
    if found is None:
        sys.stderr.write("fake peer: the evidence held no runner token\n")
        return ""
    return found.group(0) + "\n"


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


def _spawn_grandchild(detached: bool = False, seconds: str = "") -> int:
    """Start one child that sleeps, and say where it was put.

    By default there is no new session and no new process group: the grandchild
    stays in the group the runner created, so terminating that group must reach
    it. A runner that kills only its direct child leaves this process behind,
    which is the thing the orphan check is looking for.

    With `detached`, the child asks for a session of its own and so leaves the
    group this turn owns. Nothing portable can reach it after that, which is
    the honest limit one check exists to measure. Such a child is given a short
    sleep so that even a check that failed to end it cannot leave it about.
    """
    argv = [sys.executable, THIS_FILE, "hang"]
    if seconds:
        argv.append(seconds)
    child = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        start_new_session=detached,
    )
    return child.pid


def _write_pids(path: str, child_pid: int) -> None:
    """Say which two processes now exist, and force it onto the disk.

    A check outside cannot see either process id any other way: this program's
    output is held in a pipe until the call ends, and by then the processes it
    wants to watch are meant to be gone.
    """
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("PEER {0}\nCHILD {1}\n".format(os.getpid(), child_pid))
        stream.flush()
        os.fsync(stream.fileno())


def _one_path(mode: str, extra: list) -> str:
    """The single path argument a mode needs, or an empty string if missing."""
    if len(extra) != 1 or not extra[0]:
        sys.stderr.write(
            "fake peer: {0} needs exactly one path\n".format(mode)
        )
        return ""
    return extra[0]


def _run(mode: str, extra: list, evidence: str = "") -> int:
    quoted = ""
    if evidence:
        quoted = _runner_token_line(evidence)
        if not quoted:
            return 2
    body = _read_all_stdin()
    echoed = quoted + _echoed(body)

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
        seconds = MAX_SLEEP_SECONDS
        if extra:
            try:
                seconds = float(extra[0])
            except ValueError:
                sys.stderr.write("fake peer: seconds must be a number\n")
                return 2
        _sleep_bounded(seconds)
        return 0

    if mode == "spawn-child-then-hang":
        _emit("GRANDCHILD {0}\n".format(_spawn_grandchild()))
        _sleep_bounded(MAX_SLEEP_SECONDS)
        return 0

    if mode in ("slow-accept", "slow-unknown-verdict"):
        if len(extra) != 1:
            sys.stderr.write(
                "fake peer: {0} needs one seconds value\n".format(mode)
            )
            return 2
        try:
            seconds = float(extra[0])
        except ValueError:
            sys.stderr.write("fake peer: seconds must be a number\n")
            return 2
        _sleep_bounded(seconds)
        decision = "ACCEPT" if mode == "slow-accept" else "MAYBE"
        _emit(echoed + VERDICT_PREFIX + decision + "\n")
        return 0

    if mode == "read-evidence":
        if not evidence:
            sys.stderr.write("fake peer: read-evidence needs --evidence\n")
            return 2
        text = _read_evidence(evidence)
        if not text:
            return 2
        found = DIFF_CANARY.search(text)
        if found is None:
            sys.stderr.write("fake peer: the evidence held no diff canary\n")
            return 2
        _emit(quoted + found.group(0) + "\n" + VERDICT_PREFIX + "ACCEPT\n")
        return 0

    if mode == "rewrite-evidence":
        if not evidence:
            sys.stderr.write("fake peer: rewrite-evidence needs --evidence\n")
            return 2
        try:
            with open(evidence, "w", encoding="utf-8") as stream:
                stream.write("This is not the difference the runner wrote.\n")
        except OSError as exc:
            sys.stderr.write(
                "fake peer: cannot rewrite the evidence: {0}\n".format(exc)
            )
            return 2
        _emit(echoed + VERDICT_PREFIX + "ACCEPT\n")
        return 0

    if mode in ("write-pids-then-hang", "detach-child-then-hang"):
        path = _one_path(mode, extra)
        if not path:
            return 2
        detached = mode == "detach-child-then-hang"
        _write_pids(
            path,
            _spawn_grandchild(
                detached=detached, seconds="60" if detached else ""
            ),
        )
        _sleep_bounded(MAX_SLEEP_SECONDS)
        return 0

    sys.stderr.write(
        "fake peer: unknown mode {0!r}; known modes: {1}\n".format(
            mode, ", ".join(MODES)
        )
    )
    return 2


def main(argv: list) -> int:
    args = list(argv[1:])
    evidence = ""
    if args and args[0] == "--evidence":
        if len(args) < 2 or not args[1]:
            sys.stderr.write("fake peer: --evidence needs a path\n")
            return 2
        evidence = args[1]
        args = args[2:]
    if not args:
        sys.stderr.write(
            "usage: fake_peer.py [--evidence <path>] <mode> [args]; known "
            "modes: {0}\n".format(", ".join(MODES))
        )
        return 2
    return _run(args[0], args[1:], evidence)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

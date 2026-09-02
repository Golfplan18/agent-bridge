#!/usr/bin/env python3
"""A stand-in for a real coding-agent harness.

The runner's job is to start one program, hand it a Markdown body on standard
input, wait for one answer, and decide what that answer means. None of that
needs a real harness, a real subscription, or a real model - it needs a program
that behaves in one chosen way. This is that program.

It is started exactly as a real peer is:

    [sys.executable, "<path>/fake_peer.py", "<mode>", ...]

It reads the whole of standard input, and then behaves according to its mode.
Every mode exists because some check needs it, and there are only two families
of them.

The first is about what a peer says: a good answer, which is whatever it was
given handed straight back; no answer at all; an answer that is nothing but
whitespace; and, for a harness that takes the message on its command line
rather than on standard input, the final argument handed straight back, with a
complaint appended if anything arrived on standard input as well. Between them
they are every shape the runner has to tell apart when it decides whether there
is anything to publish, and both ways a body can travel.

The second is about what a peer leaves behind: a program that exits badly, one
that will not stop, one that starts a child and then will not stop, and two that
report their own process id and their child's into a file the check names, so a
check can watch those exact processes rather than guessing at them. Of those
last two, one leaves the child in the process group the turn owns and one lets
it escape into a session of its own, which is the stated limit of what any
cleanup can reach.

It imports nothing outside the standard library, touches nothing except the
input it is given, the file it is told to write its process ids into, and the
output streams it is handed. It never sleeps without a bound.

SPDX-License-Identifier: Unlicense
"""

import os
import subprocess
import sys
import time

#: Every supported mode, in the order the docstring above describes them.
MODES = (
    "plain",
    "last-argument",
    "empty",
    "whitespace",
    "fail",
    "hang",
    "spawn-child-then-hang",
    "write-pids-then-hang",
    "detach-child-then-hang",
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


def _run(mode: str, extra: list) -> int:
    body = _read_all_stdin()
    echoed = _echoed(body)

    if mode == "plain":
        _emit(echoed)
        return 0

    if mode == "last-argument":
        # The body travelled as the final argument; hand that back exactly.
        # Standard input should have carried nothing, and if it did, say so
        # where a check will see it.
        _emit(_echoed(extra[-1]) if extra else "")
        if body:
            _emit("STDIN WAS NOT EMPTY\n")
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
    if not args:
        sys.stderr.write(
            "usage: fake_peer.py <mode> [args]; known modes: {0}\n".format(
                ", ".join(MODES)
            )
        )
        return 2
    return _run(args[0], args[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Starting one program, waiting for one answer, and leaving nothing behind.

Everything Agent Bridge starts - a peer harness or the local Git program - is
started here, the same way, under the same deadline, and cleaned up the same
way. There is one function worth understanding, `run_bounded`, and the care in
it is all about two questions: what may be started, and what must be gone
afterwards.

**What may be started.** A fixed list of arguments, with no shell anywhere. The
outgoing Markdown goes down the program's standard input and nowhere else, so
text a peer or a plan may have influenced never becomes part of a command.

**What must be gone afterwards.** The child is started as its own session
leader, which makes it the leader of a brand new process group containing it and
anything it starts. That group is the exact set of processes this turn owns.
Cleanup signals that group and nothing else - never a name, never a scan of
unrelated processes, never a guess. Before signalling anything, the code
confirms that the group's number really is the child's own process id, which is
the check that makes it impossible to signal the group Agent Bridge itself is
running in.

**What ends the waiting.** Three things can: the program answers, the deadline
passes, or somebody stops Agent Bridge - with an interrupt from the keyboard, or
with a termination or hangup signal. All three become exceptions, so all three
leave by the same route and the same cleanup runs. The signal handlers are put
back exactly as they were found on the way out, and where they cannot be
installed at all - because this is not the main thread - the turn goes ahead
without them, which is better than refusing to work.

**What cannot be cleaned up, honestly.** Being killed outright with `SIGKILL`
cannot be caught by any program, a machine that loses power runs no cleanup
code, and a child that deliberately puts itself into its own session has left
the group this turn owns and can no longer be reached by signalling that group.
None of the three can be controlled portably, so none of them is pretended
about. The consequence for connectors is concrete: a harness command-line
program that daemonizes during a turn puts its work beyond this cleanup and must
therefore fail qualification.

The deadline covers the useful work: prechecks, evidence generation, the call
and the answer. Cleanup afterwards gets its own separate bounded grace, because
a deadline that has already run out cannot be used to decide how long to wait
for a process to die.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from typing import Iterable, Iterator, NamedTuple, Optional, Sequence, Tuple

from .errors import BridgeError, Failure

#: The default whole-turn deadline, in seconds, when a caller names none.
DEFAULT_TIMEOUT_SECONDS = 900.0

#: How long a polite termination is given to empty the process group. Bounded
#: separately from the call deadline, which may already have run out.
CLEANUP_GRACE_SECONDS = 5.0

#: How long an unconditional kill is then given. After this the turn reports
#: `CLEANUP_FAILURE` rather than pretending the group is gone.
ESCALATION_GRACE_SECONDS = 5.0

#: Interval between the short looks that ask whether the group has emptied.
POLL_SECONDS = 0.02


class Deadline(object):
    """One deadline for a whole turn, made once and passed down.

    Created at the start of a `run` and handed to every bounded step, so
    prechecks, review-evidence generation, the peer call and reading the answer
    all draw on the same budget rather than each getting a fresh one.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = float(seconds)
        self._started = time.monotonic()

    def remaining(self) -> float:
        """Seconds left; zero or less once the deadline has passed."""
        return self.seconds - (time.monotonic() - self._started)

    def check(self, detail: Optional[str] = None) -> None:
        """Stop now if the deadline has already passed."""
        if self.remaining() <= 0.0:
            raise BridgeError(Failure.TIMEOUT, detail=detail)


class CompletedCall(NamedTuple):
    """What one bounded call produced."""

    returncode: int
    stdout: str
    stderr: str


class SignalStop(Exception):
    """Somebody asked this turn to stop while it was under way.

    Raised from inside a signal handler so that a termination or a hangup
    leaves by the ordinary route - through the cleanup that terminates the
    process group, deletes the review evidence and releases the session lock -
    instead of ending the process where it stands. It is deliberately not one
    of the internal failures: nothing went wrong with the turn, it was stopped.
    """

    def __init__(self, number: int) -> None:
        super().__init__(
            "Agent Bridge was stopped by signal {0}, so the turn did not "
            "finish and the Git finish line stays locked.".format(number)
        )
        self.number = number


#: The two signals a turn turns into `SignalStop`. An interrupt from the
#: keyboard already arrives as `KeyboardInterrupt` and needs nothing added.
STOP_SIGNALS: Tuple[int, ...] = (signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def stopped_by_signal() -> Iterator[None]:
    """Make termination and hangup raise, and put the handlers back after.

    Installing a handler is only possible on the main thread. Somewhere else it
    is impossible rather than wrong, so the block runs without them: losing a
    tidy exit is a smaller harm than refusing to do the work at all.
    """

    def stop(number, frame):
        """Leave by raising, so the cleanup around the caller still runs."""
        raise SignalStop(number)

    installed = []
    try:
        for number in STOP_SIGNALS:
            installed.append((number, signal.signal(number, stop)))
    except (OSError, ValueError):
        pass
    try:
        yield
    finally:
        for number, previous in reversed(installed):
            try:
                signal.signal(number, previous)
            except (OSError, ValueError):
                pass


class PeerTimeout(BridgeError):
    """`TIMEOUT`, carrying what the program had already said.

    A timed-out call still produced evidence: whatever the program wrote before
    the deadline, and the process id of the group that was terminated. Both are
    kept on the exception because they are the only account of what happened,
    and because confirming that nothing was orphaned means naming processes by
    their own identity rather than by what they were called.
    """

    def __init__(
        self, pid: int, stdout: str, stderr: str, detail: Optional[str] = None
    ) -> None:
        super().__init__(Failure.TIMEOUT, detail=detail)
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr


def _group_gone(pgid: int) -> bool:
    """Is the process group empty? Signal zero asks without disturbing it.

    A process that has died but not yet been collected by its parent still
    answers, so the direct child must be collected before this answer means
    anything.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


def _await_group_gone(pgid: int, grace: float) -> bool:
    limit = time.monotonic() + grace
    while True:
        if _group_gone(pgid):
            return True
        if time.monotonic() >= limit:
            return False
        time.sleep(POLL_SECONDS)


def _signal_group(pgid: int, number: int) -> None:
    try:
        os.killpg(pgid, number)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="process group {0}: {1}".format(pgid, exc),
        )


def _reap(process: "subprocess.Popen", grace: float) -> None:
    """Collect the direct child so it stops answering signals as a corpse."""
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def _close_streams(process: "subprocess.Popen") -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _own_group(process: "subprocess.Popen") -> int:
    """The process group this turn owns, confirmed to be the child's own.

    Read once, immediately after the child has been started. The child was
    asked to become a session leader, so its group number must equal its own
    process id. If it does not, this turn does not own a group it can safely
    signal, and it says so instead of signalling anything.
    """
    try:
        pgid = os.getpgid(process.pid)
    except OSError as exc:
        _close_streams(process)
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="cannot read the process group of {0}: {1}".format(
                process.pid, exc
            ),
        )
    if pgid != process.pid:
        _close_streams(process)
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "process {0} is in group {1}, which this turn does not own, "
                "so nothing was signalled".format(process.pid, pgid)
            ),
        )
    return pgid


def _cleanup_group(process: "subprocess.Popen", pgid: int) -> None:
    """Terminate exactly the group this turn started, and confirm it is empty.

    Asks politely first, collects the direct child so a corpse cannot be
    mistaken for a survivor, and escalates against the same group only. If
    anything in the group is still there after the escalation grace, that is
    `CLEANUP_FAILURE`: an unreported survivor would be worse than a visible
    failure.
    """
    if pgid != process.pid:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="refusing to signal group {0}".format(pgid),
        )
    _signal_group(pgid, signal.SIGTERM)
    _reap(process, CLEANUP_GRACE_SECONDS)
    if _await_group_gone(pgid, CLEANUP_GRACE_SECONDS):
        return
    _signal_group(pgid, signal.SIGKILL)
    _reap(process, ESCALATION_GRACE_SECONDS)
    if _await_group_gone(pgid, ESCALATION_GRACE_SECONDS):
        return
    raise BridgeError(
        Failure.CLEANUP_FAILURE,
        detail="process group {0} still has a member".format(pgid),
    )


def run_bounded(
    argv: Sequence[str],
    cwd: str,
    env: Iterable[Tuple[str, str]],
    stdin_text: str,
    deadline: Deadline,
    spawn_failure: Failure = Failure.MISSING_CLI,
) -> CompletedCall:
    """Run one program to completion inside the deadline, and clean up after it.

    `argv` is a fixed argument vector run without a shell. `stdin_text` is
    written to the program's standard input and the input is then closed, so a
    program that reads to end-of-file gets everything and then stops waiting.

    Raises `PeerTimeout` (a `TIMEOUT`) when the deadline passes first, the given
    `spawn_failure` when the program could not be started at all, and
    `CLEANUP_FAILURE` when something this turn started outlived it. Raises
    `SignalStop` when somebody terminates or hangs up Agent Bridge while the
    program is running. Cleanup runs on every one of those exit paths, and on
    success too.
    """
    remaining = deadline.remaining()
    if remaining <= 0.0:
        raise BridgeError(Failure.TIMEOUT, detail=" ".join(argv[:2]))
    payload = stdin_text.encode("utf-8")
    timed_out = False
    stdout = b""
    stderr = b""
    # The handlers go on before the child does, so there is no moment where a
    # process exists that a signal could leave behind.
    with stopped_by_signal():
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise BridgeError(spawn_failure, detail=str(exc))

        pgid = _own_group(process)
        try:
            try:
                stdout, stderr = process.communicate(
                    input=payload, timeout=remaining
                )
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            try:
                _cleanup_group(process, pgid)
            finally:
                if timed_out:
                    # The group is gone, so the pipes are at end-of-file and
                    # this returns at once with everything the program managed
                    # to say.
                    try:
                        stdout, stderr = process.communicate(
                            timeout=ESCALATION_GRACE_SECONDS
                        )
                    except (subprocess.TimeoutExpired, ValueError, OSError):
                        stdout, stderr = b"", b""
                _close_streams(process)

    out_text = stdout.decode("utf-8", "replace") if stdout else ""
    err_text = stderr.decode("utf-8", "replace") if stderr else ""
    if timed_out:
        raise PeerTimeout(
            pid=process.pid,
            stdout=out_text,
            stderr=err_text,
            detail="{0} after {1:.0f}s".format(argv[0], deadline.seconds),
        )
    return CompletedCall(
        returncode=process.returncode, stdout=out_text, stderr=err_text
    )

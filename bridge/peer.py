"""Starting one program, waiting for one answer, and leaving nothing behind.

Everything Agent Bridge starts is started here, the same way, under the same
deadline, and cleaned up the same way. There is one function worth
understanding, `run_bounded`, and the care in it is all about two questions:
what may be started, and what must be gone afterwards.

**What may be started.** A fixed list of arguments, with no shell anywhere. The
outgoing Markdown goes down the program's standard input, or, for the one kind
of connector that has proved its harness has no standard-input path, arrives
bound to an option as a single final argument that the runner composed; either
way no shell ever sees it, so text a peer or a plan may have influenced never
becomes part of a command.

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
back exactly as they were found on the way out.

**When a stop raises, and when it waits.** Raising where it lands is right for
exactly one stretch of a turn - the wait for the program's answer - and wrong
for the rest of it.

It is wrong while the child is being created: between the operating system
making the process and this code knowing which group it belongs to, a stop that
raised would leave a running program that nothing had yet taken responsibility
for. It is wrong during cleanup: a second stop arriving while the group is being
emptied would abandon the emptying half done, which is the opposite of what the
person pressing the key wants.

So it is arranged the other way round from what might be expected. A stop is
deferred - written down rather than raised - for the whole life of the child,
and one window is opened, around the wait, where it raises immediately. That
window sits inside the cleanup that catches what it raises. There is therefore
no instruction anywhere between the child appearing and its group being empty at
which a stop can leave without cleanup having run. Once it has, the stop that
was written down is raised.

All three stops go through this, the keyboard interrupt included. Ctrl-C still
raises `KeyboardInterrupt` exactly as it always did, and is handled here for one
reason only: a handler of our own can be made to wait through those moments,
where Python's own cannot. Leaving it out would leave the commonest way of
stopping a program the one way that could still strand a peer.

Deferral changes when a stop is raised, never whether.

**What cannot be cleaned up, honestly.** Four things. Being killed outright with
`SIGKILL` cannot be caught by any program. A machine that loses power runs no
cleanup code. A child that deliberately puts itself into its own session has
left the group this turn owns and can no longer be reached by signalling that
group. And a turn run off the main thread has no handlers at all: Python only
ever delivers a signal to the main thread, and only the main thread may install
a handler, so a termination signal there does whatever the surrounding program
already arranged - which, by default, ends the process at once and leaves the
peer running. The turn still goes ahead in that case, because refusing to work
would be worse, but the tidy exit is not available and is not claimed.

None of the four can be controlled portably, so none of them is pretended about.
The consequence for connectors is concrete: a harness command-line program that
daemonizes during a turn puts its work beyond this cleanup and must therefore
fail qualification.

The deadline covers the useful work: prechecks, the call and the answer. Cleanup
afterwards gets its own separate bounded grace, because a deadline that has
already run out cannot be used to decide how long to wait for a process to die.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import contextlib
import errno
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
    prechecks, the peer call and reading the answer all draw on the same budget
    rather than each getting a fresh one.
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
    process group and releases the session lock - instead of ending the process
    where it stands. It is deliberately not one of the internal failures:
    nothing went wrong with the turn, it was stopped.
    """

    def __init__(self, number: int) -> None:
        super().__init__(
            "Agent Bridge was stopped by signal {0}, so the turn did not "
            "finish.".format(number)
        )
        self.number = number


#: The two signals a turn turns into `SignalStop`.
STOP_SIGNALS: Tuple[int, ...] = (signal.SIGTERM, signal.SIGHUP)

#: An interrupt from the keyboard goes on raising `KeyboardInterrupt`, exactly
#: as it always did. It is handled here all the same, and for one reason only:
#: a handler of our own can be made to wait through the two moments below,
#: where Python's cannot. Ctrl-C is how a person ordinarily stops a program, so
#: leaving it out would leave the commonest stop able to strand a peer.
INTERRUPT_SIGNALS: Tuple[int, ...] = (signal.SIGINT,)

#: Every signal handled here, in the order they are installed.
HANDLED_SIGNALS: Tuple[int, ...] = STOP_SIGNALS + INTERRUPT_SIGNALS


def _stop_exception(number: int) -> BaseException:
    """What a given signal leaves by. Interrupts keep their own exception."""
    if number in INTERRUPT_SIGNALS:
        return KeyboardInterrupt()
    return SignalStop(number)


class StopWatch(object):
    """When a stop raises where it lands, and when it waits its turn.

    A stop that raises where it lands is what makes it leave through the cleanup
    around the caller instead of ending the process where it stands. That is
    right for exactly one stretch of a turn - the wait for the program's answer
    - and wrong for the rest of it, where a running child either has nothing yet
    responsible for it or is in the middle of being cleaned up.

    So the arrangement is the other way round from what it might seem. For the
    whole life of the child a stop is deferred: written down, and raised once
    the stretch it arrived in is over. `allowing()` opens the one window where
    it raises immediately, and that window sits inside the cleanup that catches
    it. There is therefore no instruction anywhere between the child appearing
    and the group being empty at which a stop can leave without cleanup running.

    Deferral changes when a stop is raised, never whether. Only the first is
    remembered, because they all mean the same thing: the turn is being stopped,
    and it leaves once.

    The state lives on the object, not in the module, so it belongs to exactly
    one `stopped_by_signal` region. Nothing can be left behind for a later turn
    to trip over.
    """

    def __init__(self) -> None:
        self._deferrals = 0
        self._pending = None  # type: Optional[int]

    def handle(self, number, frame) -> None:
        """The signal handler itself: raise now, or write it down for later."""
        if self._deferrals > 0:
            if self._pending is None:
                self._pending = number
            return
        raise _stop_exception(number)

    @contextlib.contextmanager
    def deferring(self) -> Iterator[None]:
        """Inside this block a stop is written down instead of being raised."""
        self._deferrals += 1
        try:
            yield
        finally:
            self._deferrals -= 1

    @contextlib.contextmanager
    def allowing(self) -> Iterator[None]:
        """Inside this block a stop raises again. Only valid inside `deferring`.

        Reopening the door is what makes a stop interrupt the wait for an
        answer, which is the whole point of handling one. The block must sit
        inside a `deferring()` block whose cleanup will catch what it raises.

        A stop that arrived before the door opened is raised as it opens, rather
        than left to be noticed later. Otherwise a turn already told to stop
        would go on and wait out its whole deadline for an answer nobody is
        waiting for any more.
        """
        self._deferrals -= 1
        try:
            self.raise_if_stopped()
            yield
        finally:
            self._deferrals += 1

    def raise_if_stopped(self) -> None:
        """Raise a stop that arrived while it was being deferred, if one did."""
        number, self._pending = self._pending, None
        if number is not None:
            raise _stop_exception(number)


@contextlib.contextmanager
def stopped_by_signal() -> Iterator[StopWatch]:
    """Make termination and hangup raise, and put the handlers back after.

    Installing a handler is only possible on the main thread. Somewhere else it
    is impossible rather than wrong, so the block runs without them: losing a
    tidy exit is a smaller harm than refusing to do the work at all. The watch
    is still yielded in that case and simply never fires, so callers need no
    second shape of code for it.
    """
    watch = StopWatch()
    installed = []
    try:
        for number in HANDLED_SIGNALS:
            installed.append((number, signal.signal(number, watch.handle)))
    except (OSError, ValueError):
        pass
    try:
        yield watch
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
    process = None  # type: Optional[subprocess.Popen]
    pgid = None  # type: Optional[int]
    # The handlers go on before the child does, and a stop is deferred for the
    # whole life of the child: while it is being started, while its group is
    # being read, and while that group is being emptied. The one window where a
    # stop raises where it lands is the wait for the answer, and that window
    # sits inside the cleanup that catches what it raises. So there is no
    # instruction anywhere between the child appearing and its group being
    # empty at which a stop can leave without cleanup having run.
    with stopped_by_signal() as watch:
        with watch.deferring():
            try:
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
                    # The one refusal that is about the call rather than the
                    # program: the kernel would not build an argument block
                    # this large. It is the transport's failure and is named
                    # as such, not as a missing program or a failed peer.
                    if exc.errno == errno.E2BIG:
                        raise BridgeError(
                            Failure.USAGE_ERROR,
                            detail=(
                                "the operating system refused to start {0} "
                                "because its argument list and environment "
                                "together were too long ({1}); send a "
                                "shorter message, or use a peer that reads "
                                "standard input".format(argv[0], exc)
                            ),
                        )
                    raise BridgeError(spawn_failure, detail=str(exc))
                pgid = _own_group(process)
                with watch.allowing():
                    try:
                        stdout, stderr = process.communicate(
                            input=payload, timeout=remaining
                        )
                    except subprocess.TimeoutExpired:
                        timed_out = True
            finally:
                if process is not None:
                    try:
                        # Still deferred, so a second stop cannot abandon this
                        # half done. When the group was never this turn's to
                        # signal, `_own_group` has already said so and nothing
                        # here signals anything.
                        if pgid is not None:
                            _cleanup_group(process, pgid)
                    finally:
                        if timed_out:
                            # The group is gone, so the pipes are at end-of-file
                            # and this returns at once with everything the
                            # program managed to say.
                            try:
                                stdout, stderr = process.communicate(
                                    timeout=ESCALATION_GRACE_SECONDS
                                )
                            except (
                                subprocess.TimeoutExpired,
                                ValueError,
                                OSError,
                            ):
                                stdout, stderr = b"", b""
                        _close_streams(process)
        # Nothing this turn started is left, so a stop that arrived while it was
        # being deferred can be raised now without leaving anything behind.
        watch.raise_if_stopped()

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

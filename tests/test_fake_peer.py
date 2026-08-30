"""What one turn must do, and must never do, proved against a fake peer.

None of this needs a real coding-agent harness, a subscription or a model. Every
behavior worth proving here is about files, locks and processes: that a message
is either whole or absent, that two turns cannot both hold a session, that a
lock left behind by a process that was killed outright blocks nobody, that being
told to stop cleans up what a turn started, and that nothing this turn started
is still running when it returns.

Everything below exercises the real thing. Real processes are started, real
files are written, real locks are taken out in separate processes, real signals
are sent, and a real process is killed with no chance to tidy up. The two
exceptions are the forced publication failures, which cannot be produced on
demand any other way and are made to happen by breaking the rename, and then
the flush that follows it, for the length of one call.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bridge import gitgate, peer, record, runner, session  # noqa: E402
from bridge.connectors import PeerCommand  # noqa: E402
from bridge.errors import BridgeError, Failure  # noqa: E402
from bridge.locking import lock_path, session_lock  # noqa: E402

FAKE_PEER = os.path.join(REPO_ROOT, "tests", "fake_peer.py")

#: How long a check waits for something that must happen very soon.
SHORT_WAIT = 10.0
POLL = 0.02

#: A program that takes the session lock, says so, and holds it until either its
#: input closes or it is killed. It is the simplest way to have the lock held by
#: a process this one can end abruptly.
LOCK_HOLDER = (
    "import fcntl, os, sys\n"
    "handle = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
    "fcntl.flock(handle, fcntl.LOCK_EX)\n"
    "sys.stdout.write('LOCKED\\n')\n"
    "sys.stdout.flush()\n"
    "sys.stdin.readline()\n"
)

GRANDCHILD_LINE = re.compile(r"^GRANDCHILD (\d+)$", re.MULTILINE)

#: How the fake peer reports the two processes a stop signal has to reach.
PID_LINE = re.compile(r"^(PEER|CHILD) (\d+)$", re.MULTILINE)

#: One bounded call, in a process of its own, so a check can signal the thing
#: doing the waiting rather than the check itself. It exits 3 when it is
#: stopped, which is how a check tells "cleaned up after a signal" apart from
#: "died some other way".
SIGNAL_DRIVER = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from bridge import peer\n"
    "try:\n"
    "    peer.run_bounded(\n"
    "        argv=(sys.executable, sys.argv[2], sys.argv[3], sys.argv[4]),\n"
    "        cwd=sys.argv[1],\n"
    "        env=tuple(os.environ.items()),\n"
    "        stdin_text='Start something and then stop answering.\\n',\n"
    "        deadline=peer.Deadline(60.0),\n"
    "    )\n"
    "except peer.SignalStop:\n"
    "    sys.exit(3)\n"
)


def _wait_for(condition, timeout=SHORT_WAIT):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if condition():
            return True
        time.sleep(POLL)
    return condition()


def _process_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _group_gone(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


class TurnBehavior(unittest.TestCase):
    """One session, one fake peer, and the things a turn owes the record."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-check-")
        self.session_dir = os.path.join(self.temp, "session")
        record.record(
            self.session_dir,
            "session-create",
            "Prove the runner without spending a real harness call.\n",
            local="codex",
            peer="claude",
            workflow="planning",
        )
        self.holders = []
        self.drivers = []
        self.evidence_before = self._evidence_files()

    def tearDown(self):
        for holder in self.holders:
            if holder.poll() is None:
                holder.kill()
            holder.wait()
            for stream in (holder.stdin, holder.stdout):
                if stream is not None:
                    stream.close()
        for driver in self.drivers:
            if driver.poll() is None:
                driver.kill()
            driver.wait()
            for stream in (driver.stdout, driver.stderr):
                if stream is not None:
                    stream.close()
        self.assertEqual(
            self._evidence_files(),
            self.evidence_before,
            "a review-evidence file outlived the turn that made it",
        )
        shutil.rmtree(self.temp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def _evidence_files(self):
        pattern = os.path.join(
            tempfile.gettempdir(), gitgate.REVIEW_EVIDENCE_PREFIX + "*"
        )
        return set(glob.glob(pattern))

    def _builder(self, mode, *extra):
        """What the runner calls to compose the command for one turn.

        The runner hands a builder the review-evidence path when it has one,
        and this turn's deadline so that anything the builder starts is bounded
        by it. None of the turns here are reviews, so what arrives is None, and
        this builder declares that it granted the peer no project and no
        evidence file - which is what the runner requires it to say.
        """
        argv = (sys.executable, FAKE_PEER, mode) + tuple(extra)

        def build(evidence_path, deadline):
            return PeerCommand(
                argv=argv,
                cwd=self.temp,
                env=tuple(os.environ.items()),
                project_root=None,
                review_evidence=evidence_path,
            )

        return build

    def _start_signal_driver(self, mode, pid_path):
        """One bounded call waiting in its own process, ready to be signalled."""
        driver = subprocess.Popen(
            [
                sys.executable,
                "-c",
                SIGNAL_DRIVER,
                REPO_ROOT,
                FAKE_PEER,
                mode,
                pid_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        self.drivers.append(driver)
        return driver

    def _await_reported_pids(self, pid_path):
        """The peer's own process id and its child's, once both are running."""
        reported = {}

        def both_reported():
            try:
                with open(pid_path, encoding="utf-8") as stream:
                    text = stream.read()
            except OSError:
                return False
            found = dict(
                (name, int(number))
                for name, number in PID_LINE.findall(text)
            )
            if len(found) != 2:
                return False
            reported.update(found)
            return True

        self.assertTrue(
            _wait_for(both_reported),
            "the fake peer never reported the processes it started",
        )
        peer_pid = reported["PEER"]
        child_pid = reported["CHILD"]
        self.assertTrue(
            _wait_for(lambda: not _process_gone(peer_pid)),
            "the peer never started",
        )
        self.assertTrue(
            _wait_for(lambda: not _process_gone(child_pid)),
            "the process the peer started never ran",
        )
        return peer_pid, child_pid

    def _messages(self):
        return sorted(os.listdir(session.messages_dir(self.session_dir)))

    def _leftover_temporaries(self):
        return [
            name
            for name in os.listdir(session.messages_dir(self.session_dir))
            if name.startswith(session.TEMP_PREFIX)
        ]

    def _start_lock_holder(self):
        holder = subprocess.Popen(
            [sys.executable, "-c", LOCK_HOLDER, lock_path(self.session_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            shell=False,
        )
        self.holders.append(holder)
        self.assertEqual(holder.stdout.readline(), b"LOCKED\n")
        return holder

    def _turn(self, mode, *extra, **kwargs):
        timeout = kwargs.pop("timeout", 30.0)
        body = kwargs.pop("body", "Please answer this.\n")
        return runner.run_turn(
            self.session_dir,
            "claude",
            body,
            self._builder(mode, *extra),
            timeout,
        )

    def _expect(self, failure, mode, *extra, **kwargs):
        with self.assertRaises(BridgeError) as caught:
            self._turn(mode, *extra, **kwargs)
        self.assertEqual(caught.exception.failure, failure)
        return caught.exception

    # -- the ordinary case -------------------------------------------------

    def test_round_trip_publishes_request_and_response(self):
        result = self._turn("plain", body="Consider the plan.\n")

        self.assertEqual(result.request_sequence, 1)
        self.assertEqual(result.response_sequence, 2)
        self.assertIsNone(result.verdict)
        self.assertFalse(result.git_unlocked)
        self.assertEqual(
            self._messages(),
            ["0001-local-to-peer.md", "0002-peer-to-local.md"],
        )

        with open(
            session.message_path(
                self.session_dir, 1, session.LOCAL_TO_PEER_SUFFIX
            ),
            encoding="utf-8",
        ) as stream:
            request = stream.read()
        self.assertEqual(
            request,
            "# Message 0001\nFrom: codex\nTo: claude\n\n## Body\n\n"
            "Consider the plan.\n",
        )

        with open(result.response_path, encoding="utf-8") as stream:
            response = stream.read()
        self.assertEqual(
            response,
            "# Message 0002\nFrom: claude\nTo: codex\n\n## Body\n\n"
            "Consider the plan.\n",
        )
        self.assertNotIn("Review-", response)
        self.assertEqual(self._leftover_temporaries(), [])

    # -- atomic publication -------------------------------------------------

    def test_concurrent_publication_is_whole_and_uniquely_numbered(self):
        workers = []
        for index in range(6):
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bridge",
                    "record",
                    "--session",
                    self.session_dir,
                    "--kind",
                    "technical-error",
                ],
                cwd=REPO_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            workers.append((index, worker))

        published = 0
        busy = 0
        for index, worker in workers:
            out, err = worker.communicate(
                input="Concurrent record {0}.\n".format(index).encode("utf-8")
            )
            if worker.returncode == 0:
                published += 1
            else:
                self.assertIn(b"holding this session's lock", err)
                busy += 1

        self.assertGreaterEqual(published, 1)
        self.assertEqual(published + busy, len(workers))

        names = self._messages()
        self.assertEqual(len(names), published)
        self.assertEqual(
            [name.split("-")[0] for name in names],
            ["{0:04d}".format(number) for number in range(1, published + 1)],
        )
        for name in names:
            path = os.path.join(session.messages_dir(self.session_dir), name)
            with open(path, encoding="utf-8") as stream:
                text = stream.read()
            self.assertTrue(text.startswith("# Message "))
            self.assertIn("Record: technical-error\n", text)
            self.assertIn("\n## Body\n\n", text)
            self.assertTrue(text.rstrip().endswith("."))
        self.assertEqual(self._leftover_temporaries(), [])

    def test_failure_during_publication_leaves_nothing_behind(self):
        target = session.message_path(
            self.session_dir, 1, session.LOCAL_RECORD_SUFFIX
        )
        with mock.patch("os.replace", side_effect=OSError("forced failure")):
            with self.assertRaises(BridgeError) as caught:
                session.publish(target, "# Message 0001\nnever arrives\n")
        self.assertEqual(caught.exception.failure, Failure.PUBLICATION_FAILURE)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(self._messages(), [])
        self.assertEqual(self._leftover_temporaries(), [])

    def test_a_rename_that_worked_is_never_reported_as_nothing_published(self):
        """The message is there, so the failure has to say the file is there."""
        target = session.message_path(
            self.session_dir, 1, session.LOCAL_RECORD_SUFFIX
        )
        text = "# Message 0001\nRecord: user-correction\nFrom: codex\n"
        with mock.patch.object(
            session, "_fsync_directory", side_effect=OSError("forced failure")
        ):
            with self.assertRaises(BridgeError) as caught:
                session.publish(target, text)

        self.assertEqual(
            caught.exception.failure, Failure.PUBLICATION_NOT_FLUSHED
        )
        self.assertIn(target, str(caught.exception))
        self.assertTrue(
            os.path.exists(target), "the published message is not there"
        )
        with open(target, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), text)
        self.assertEqual(self._leftover_temporaries(), [])

    # -- the lock ----------------------------------------------------------

    def test_a_second_acquirer_is_busy_and_changes_nothing(self):
        self._turn("plain")
        before = self._messages()
        with open(
            session.session_file(self.session_dir), encoding="utf-8"
        ) as stream:
            session_before = stream.read()

        self._start_lock_holder()

        with self.assertRaises(BridgeError) as caught:
            with session_lock(self.session_dir):
                self.fail("the lock was granted twice")
        self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)

        with self.assertRaises(BridgeError) as caught:
            record.record(self.session_dir, "user-correction", "Blocked.\n")
        self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)

        self.assertEqual(self._messages(), before)
        with open(
            session.session_file(self.session_dir), encoding="utf-8"
        ) as stream:
            self.assertEqual(stream.read(), session_before)
        self.assertEqual(self._leftover_temporaries(), [])

    def test_lock_survives_nothing_when_its_owner_is_killed(self):
        holder = self._start_lock_holder()

        with self.assertRaises(BridgeError) as caught:
            with session_lock(self.session_dir):
                self.fail("the lock was granted twice")
        self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)

        os.kill(holder.pid, signal.SIGKILL)
        holder.wait()
        self.assertTrue(_wait_for(lambda: _process_gone(holder.pid)))

        # No stale-lock service, no lease, no owner check, nothing stolen: the
        # kernel dropped the lock when the killed process's descriptor closed.
        with session_lock(self.session_dir):
            pass
        path = record.record(
            self.session_dir, "user-correction", "After the abrupt end.\n"
        )
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self._leftover_temporaries(), [])

    # -- failures publish no answer ----------------------------------------

    def test_timeout_publishes_no_response(self):
        self._expect(Failure.TIMEOUT, "hang", timeout=1.0)
        self.assertEqual(self._messages(), ["0001-local-to-peer.md"])
        self.assertEqual(self._leftover_temporaries(), [])

    def test_nonzero_exit_publishes_no_response(self):
        error = self._expect(Failure.PEER_FAILURE, "fail")
        self.assertIn("deliberate failure", str(error))
        self.assertEqual(self._messages(), ["0001-local-to-peer.md"])

    def test_no_output_at_all_publishes_no_response(self):
        self._expect(Failure.EMPTY_RESPONSE, "empty", body="Say nothing.\n")
        self.assertEqual(self._messages(), ["0001-local-to-peer.md"])

    def test_whitespace_only_output_publishes_no_response(self):
        self._expect(Failure.EMPTY_RESPONSE, "whitespace", body="Say air.\n")
        self.assertEqual(self._messages(), ["0001-local-to-peer.md"])

    # -- nothing is left running -------------------------------------------

    def test_the_task_group_is_terminated_and_nothing_is_orphaned(self):
        with self.assertRaises(peer.PeerTimeout) as caught:
            peer.run_bounded(
                argv=(sys.executable, FAKE_PEER, "spawn-child-then-hang"),
                cwd=self.temp,
                env=tuple(os.environ.items()),
                stdin_text="Start something and then stop answering.\n",
                deadline=peer.Deadline(1.0),
            )
        timeout = caught.exception
        self.assertEqual(timeout.failure, Failure.TIMEOUT)

        found = GRANDCHILD_LINE.search(timeout.stdout)
        self.assertIsNotNone(
            found, "the fixture did not report the process it started"
        )
        grandchild = int(found.group(1))

        self.assertTrue(
            _wait_for(lambda: _process_gone(timeout.pid)),
            "the peer process is still there",
        )
        self.assertTrue(
            _wait_for(lambda: _process_gone(grandchild)),
            "the process the peer started is still there",
        )
        self.assertTrue(
            _wait_for(lambda: _group_gone(timeout.pid)),
            "the task-owned process group still has a member",
        )

    def test_being_told_to_stop_cleans_up_the_peer_and_its_child(self):
        """Termination and hangup leave by the same door Ctrl-C already used."""
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number):
                pid_path = os.path.join(
                    self.temp, "pids-{0}.txt".format(number)
                )
                driver = self._start_signal_driver(
                    "write-pids-then-hang", pid_path
                )
                peer_pid, child_pid = self._await_reported_pids(pid_path)

                os.kill(driver.pid, number)
                driver.wait(timeout=SHORT_WAIT)
                self.assertEqual(
                    driver.returncode,
                    3,
                    driver.stderr.read().decode("utf-8", "replace"),
                )
                self.assertTrue(
                    _wait_for(lambda: _process_gone(peer_pid)),
                    "the peer is still there",
                )
                self.assertTrue(
                    _wait_for(lambda: _process_gone(child_pid)),
                    "the process the peer started is still there",
                )

    def test_a_child_that_leaves_the_group_is_beyond_this_cleanup(self):
        """The stated limit, measured rather than assumed.

        A child that puts itself into a session of its own is no longer in the
        process group this turn owns, so signalling that group cannot reach it.
        This check proves the limit is real - which is why a harness program
        that daemonizes during a turn must fail qualification - and then ends
        the escaped process itself so nothing outlives the run.
        """
        pid_path = os.path.join(self.temp, "detached-pids.txt")
        driver = self._start_signal_driver(
            "detach-child-then-hang", pid_path
        )
        peer_pid, child_pid = self._await_reported_pids(pid_path)

        os.kill(driver.pid, signal.SIGTERM)
        driver.wait(timeout=SHORT_WAIT)
        self.assertTrue(
            _wait_for(lambda: _process_gone(peer_pid)),
            "the peer is still there",
        )
        self.assertFalse(
            _process_gone(child_pid),
            "the detached child was reached after all, so this check no "
            "longer measures the limitation it describes",
        )

        os.kill(child_pid, signal.SIGKILL)
        self.assertTrue(
            _wait_for(lambda: _process_gone(child_pid)),
            "the detached child could not be ended by this check either",
        )


if __name__ == "__main__":
    unittest.main()

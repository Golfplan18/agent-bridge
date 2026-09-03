"""What one turn must do, and must never do, proved against a fake peer.

None of this needs a real coding-agent harness, a subscription or a model. Every
behavior worth proving here is about files, locks and processes: that a message
is either whole or absent, that two turns cannot both hold a session, that a
lock left behind by a process that was killed outright blocks nobody, that being
told to stop cleans up what a turn started, and that nothing this turn started
is still running when it returns.

Five of the checks are about exactly *when* a stop arrives, because the awkward
moments are short ones. A stop while the child is being created, before anything
knows which process group to end. A second stop while that group is being
emptied. A stop in the instant after a message has been renamed into place,
which must never be reported as though nothing had been published. A stop inside
the removal of the temporary file a failed publication leaves behind, which must
not become a way of leaving that file on the disk. And a real termination signal
arriving in the middle of an ordinary, perfectly healthy publication - after the
temporary file has been made and before the message is moved into place - which
must not end the process outright and leave that file in the session folder.
Each is made to happen at the exact moment rather than waited for, by standing
in front of the one step it has to land on.

Two more are about publication going wrong in ways that are easy to report
untruthfully. A temporary file that will not be removed has to be named, because
otherwise nothing tells the person it is there. And a rename whose outcome
cannot be established afterwards has to be reported as exactly that - not as
"nothing was published", which would send somebody to run a command again over a
message that is already on the disk.

One is about the connector, which composes the peer command rather than handing
one over ready-made. Whatever a connector starts in order to do that - asking
the harness its version, whether it is signed in, whether the restriction
switches are there - is bounded by the turn's own deadline, and a builder that
outruns that deadline never gets a peer started and never publishes a request.

Three groups are about the one transport that is not standard input. A harness
whose program has no standard-input path may take the body as its final
command-line argument, on conditions that can all be seen from outside without
the real program: the body arrives whole as exactly one argument, behind
whatever prefix the connector said, with nothing on standard input, even when
it begins with hyphens or carries the program's own option names; a body too
large for the whole argument block, or holding a NUL byte, is refused before
any request is published and is never cut down, because a request on the disk
would say something had been sent; and a peer that is given no project is
refused one by the command itself, before anything is read or started.

The last group is about the other way something gets into a session: `record`,
which creates the immutable Format 2 header or writes one neutral note. The
two-kind list is closed, arbitrary valid initiator labels need no registration,
and legacy Format 1 structure is rejected rather than migrated.

Everything below exercises the real thing. Real processes are started, real
files are written, real locks are taken out in separate processes, real signals
are sent, and a real process is killed with no chance to tidy up. The exceptions
are the forced publication outcomes, which cannot be produced on demand any
other way and are made to happen by breaking, or by interrupting, the rename and
the flush that follows it, for the length of one call.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import contextlib
import io
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

from bridge import (  # noqa: E402
    claude,
    cli,
    codex,
    connectors,
    hermes,
    minimax,
    peer,
    qwen,
    record,
    runner,
    session,
    zcode,
)
from bridge.connectors import COMMAND_LINE_BODY_LIMIT, PeerCommand  # noqa: E402
from bridge.errors import BridgeError, Failure  # noqa: E402
from bridge.locking import lock_path, session_lock  # noqa: E402
from bridge.peer import CompletedCall  # noqa: E402
from tests import release_conformance  # noqa: E402

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

#: The body every driver below shares: one bounded call, in a process of its
#: own, so a check can signal the thing doing the waiting rather than the check
#: itself. It exits 3 when it is stopped, which is how a check tells "cleaned up
#: after a signal" apart from "died some other way".
_DRIVER_CALL = (
    "try:\n"
    "    peer.run_bounded(\n"
    "        argv=(sys.executable, sys.argv[2], sys.argv[3], sys.argv[4]),\n"
    "        cwd=sys.argv[1],\n"
    "        env=tuple(os.environ.items()),\n"
    "        stdin_text='Start something and then stop answering.\\n',\n"
    "        deadline=peer.Deadline(60.0),\n"
    "    )\n"
    "except (peer.SignalStop, KeyboardInterrupt):\n"
    "    sys.exit(3)\n"
)

_DRIVER_HEAD = (
    "import os, signal, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from bridge import peer\n"
)

#: Waits for the peer, and is stopped from outside while it waits.
SIGNAL_DRIVER = _DRIVER_HEAD + _DRIVER_CALL

#: Stopped in the one moment the child exists and nothing yet knows which group
#: it belongs to. Standing in front of the step that reads the group is the only
#: way to be sure the signal lands inside that moment rather than near it: the
#: process id is written down for the check to watch, and the stop is delivered
#: from here. Its arguments are the peer's sleep in seconds, the file to write
#: the process id into, and which signal to send.
CREATION_DRIVER = (
    _DRIVER_HEAD
    + "read_group = peer._own_group\n"
    "def own_group(process):\n"
    "    with open(sys.argv[4], 'w', encoding='utf-8') as stream:\n"
    "        stream.write('PEER {0}\\n'.format(process.pid))\n"
    "        stream.flush()\n"
    "        os.fsync(stream.fileno())\n"
    "    os.kill(os.getpid(), int(sys.argv[5]))\n"
    "    return read_group(process)\n"
    "peer._own_group = own_group\n"
    "try:\n"
    "    peer.run_bounded(\n"
    "        argv=(sys.executable, sys.argv[2], 'hang', sys.argv[3]),\n"
    "        cwd=sys.argv[1],\n"
    "        env=tuple(os.environ.items()),\n"
    "        stdin_text='Start something and then stop answering.\\n',\n"
    "        deadline=peer.Deadline(60.0),\n"
    "    )\n"
    "except (peer.SignalStop, KeyboardInterrupt):\n"
    "    sys.exit(3)\n"
)

#: Stopped a second time, while the first stop's cleanup is under way. The
#: second signal is delivered from in front of the cleanup step, so it lands
#: inside it.
SECOND_STOP_DRIVER = (
    _DRIVER_HEAD
    + "empty_group = peer._cleanup_group\n"
    "def cleanup(process, pgid):\n"
    "    os.kill(os.getpid(), signal.SIGTERM)\n"
    "    return empty_group(process, pgid)\n"
    "peer._cleanup_group = cleanup\n"
    + _DRIVER_CALL
)

#: Publishes one ordinary message and stands still in front of the rename, so a
#: termination signal sent from outside lands in the one window where the
#: temporary file exists and the message is not yet in place. Nothing is broken
#: here: this is a healthy publication interrupted at its most awkward instant.
#: It writes a marker file the moment it is waiting, so the check signals it
#: rather than guessing when to. Its arguments are the message to publish and
#: that marker path, and it exits 3 when it leaves as the stop it was.
PUBLISH_STOP_DRIVER = (
    "import os, sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from bridge import peer, session\n"
    "target, marker = sys.argv[3], sys.argv[4]\n"
    "real_replace = os.replace\n"
    "def replace(source, destination):\n"
    "    with open(marker, 'w', encoding='utf-8') as stream:\n"
    "        stream.write('READY\\n')\n"
    "        stream.flush()\n"
    "        os.fsync(stream.fileno())\n"
    "    limit = time.monotonic() + 60.0\n"
    "    while time.monotonic() < limit:\n"
    "        time.sleep(0.02)\n"
    "    return real_replace(source, destination)\n"
    "os.replace = replace\n"
    "try:\n"
    "    session.publish(\n"
    "        target,\n"
    "        '# Message 0001\\nRecord: note\\nFrom: sample-app\\n',\n"
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
            initiator="sample-app",
            peer="claude",
        )
        self.holders = []
        self.drivers = []
        self.strays = []

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
        # A check that found a process still running has already failed; this
        # makes sure the failure does not also leave the process about.
        for pid in self.strays:
            if not _process_gone(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        shutil.rmtree(self.temp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def _builder(self, mode, *extra):
        """What the runner calls to compose the command for one turn.

        The runner hands a builder this turn's deadline, so that anything the
        builder starts is bounded by it, and takes back the fixed argument
        vector to run.
        """
        argv = (sys.executable, FAKE_PEER, mode) + tuple(extra)

        def build(deadline, cwd):
            return PeerCommand(
                argv=argv,
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        return build

    def _argument_builder(self, prefix):
        """A connector that has declared the command-line transport.

        The fake peer's `last-argument` mode hands back its final argument, so
        whatever the runner bound to the prefix is exactly what comes back.
        """

        def build(deadline, cwd):
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "last-argument"),
                cwd=cwd,
                env=tuple(os.environ.items()),
                body_argument=prefix,
            )

        return build

    def _precheck_builder(self, seconds):
        """A connector whose own precheck runs longer than the turn allows.

        A real connector asks the harness its version, whether it is signed in,
        and whether the restriction switches are there. Those are programs, and
        they run inside the turn's deadline through the same bounded runner
        everything else uses. This one stands in for a precheck that hangs.
        """

        def build(deadline, cwd):
            peer.run_bounded(
                argv=(sys.executable, FAKE_PEER, "hang", str(seconds)),
                cwd=cwd,
                env=tuple(os.environ.items()),
                stdin_text="",
                deadline=deadline,
            )
            raise AssertionError("the precheck outlasted the deadline")

        return build

    def _late_builder(self):
        """A connector that finishes, but only after the deadline has passed."""

        def build(deadline, cwd):
            time.sleep(max(0.0, deadline.remaining()) + 0.05)
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "plain"),
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        return build

    def _start_driver(self, source, *args):
        """One bounded call waiting in its own process, ready to be signalled."""
        driver = subprocess.Popen(
            [sys.executable, "-c", source, REPO_ROOT, FAKE_PEER] + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        self.drivers.append(driver)
        return driver

    def _start_signal_driver(self, mode, pid_path):
        return self._start_driver(SIGNAL_DRIVER, mode, pid_path)

    def _await_reported(self, pid_path, names, running=True):
        """The process ids written into a file, once they are all there.

        With `running`, each is also confirmed to be alive, which is what a
        check needs before it signals something and watches it go. Without it,
        only the numbers are wanted: a process that is meant to be ended within
        moments of reporting itself may be gone before anything can look.
        """
        reported = {}

        def all_reported():
            try:
                with open(pid_path, encoding="utf-8") as stream:
                    text = stream.read()
            except OSError:
                return False
            found = dict(
                (name, int(number))
                for name, number in PID_LINE.findall(text)
            )
            if len(found) != len(names):
                return False
            reported.update(found)
            return True

        self.assertTrue(
            _wait_for(all_reported),
            "the processes to watch were never reported",
        )
        pids = []
        for name in names:
            pid = reported[name]
            self.strays.append(pid)
            if running:
                self.assertTrue(
                    _wait_for(lambda: not _process_gone(pid)),
                    "{0} never started".format(name),
                )
            pids.append(pid)
        return tuple(pids)

    def _await_reported_pids(self, pid_path):
        """The peer's own process id and its child's, once both are running."""
        return self._await_reported(pid_path, ("PEER", "CHILD"))

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
            body,
            timeout,
            self._builder(mode, *extra),
        )

    def _expect(self, failure, mode, *extra, **kwargs):
        with self.assertRaises(BridgeError) as caught:
            self._turn(mode, *extra, **kwargs)
        self.assertEqual(caught.exception.failure, failure)
        return caught.exception

    # -- the ordinary case -------------------------------------------------

    def test_round_trip_publishes_request_and_response(self):
        result = self._turn("plain", body="Consider the question.\n")

        self.assertEqual(result.request_sequence, 1)
        self.assertEqual(result.response_sequence, 2)
        self.assertEqual(
            self._messages(),
            ["0001-initiator-to-peer.md", "0002-peer-to-initiator.md"],
        )

        with open(
            session.message_path(
                self.session_dir, 1, session.INITIATOR_TO_PEER_SUFFIX
            ),
            encoding="utf-8",
        ) as stream:
            request = stream.read()
        self.assertEqual(
            request,
            "# Message 0001\nFrom: sample-app\nTo: claude\n\n## Body\n\n"
            "Consider the question.\n",
        )

        with open(result.response_path, encoding="utf-8") as stream:
            response = stream.read()
        self.assertEqual(
            response,
            "# Message 0002\nFrom: claude\nTo: sample-app\n\n## Body\n\n"
            "Consider the question.\n",
        )
        self.assertEqual(self._leftover_temporaries(), [])

    def test_each_call_starts_fresh_and_sends_only_its_current_body(self):
        first = self._turn("plain", body="First body with caf\u00e9.\n\n")
        second = self._turn("plain", body="Second body only.\n")

        with open(first.response_path, encoding="utf-8") as stream:
            first_text = stream.read()
        with open(second.response_path, encoding="utf-8") as stream:
            second_text = stream.read()
        self.assertIn("First body with caf\u00e9.\n\n", first_text)
        self.assertNotIn("First body", second_text)
        self.assertTrue(second_text.endswith("Second body only.\n"))
        self.assertEqual(
            self._messages(),
            [
                "0001-initiator-to-peer.md",
                "0002-peer-to-initiator.md",
                "0003-initiator-to-peer.md",
                "0004-peer-to-initiator.md",
            ],
        )

    def test_runner_uses_the_session_project_and_removes_a_neutral_directory(self):
        project_session = os.path.join(self.temp, "project-session")
        record.record(
            project_session,
            "session-create",
            "Project-bound call.\n",
            initiator="gear-3",
            peer="zcode",
            project=self.temp,
        )
        observed = []

        def project_builder(deadline, cwd):
            observed.append(cwd)
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "plain"),
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        result = runner.run_turn(
            project_session, "Use the recorded project.\n", 30.0, project_builder
        )
        self.assertEqual(observed, [self.temp])
        with open(result.response_path, encoding="utf-8") as stream:
            self.assertIn("From: zcode\nTo: gear-3\n", stream.read())

        neutral = []

        def neutral_builder(deadline, cwd):
            neutral.append(cwd)
            self.assertTrue(os.path.isdir(cwd))
            self.assertEqual(os.listdir(cwd), [])
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "plain"),
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        self._turn_with_builder(neutral_builder, "Neutral call.\n")
        self.assertEqual(len(neutral), 1)
        self.assertFalse(os.path.exists(neutral[0]))

    def test_public_run_resolves_only_the_target_stored_in_the_session(self):
        seen = []

        class FakeConnector:
            COURIER_ONLY = False

            @staticmethod
            def build_command(deadline, cwd):
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "plain"),
                    cwd=cwd,
                    env=tuple(os.environ.items()),
                )

        def resolve(peer_id):
            seen.append(peer_id)
            return FakeConnector

        output = io.StringIO()
        with mock.patch.object(runner.connectors, "resolve", resolve):
            with mock.patch("sys.stdout", output):
                with mock.patch("sys.stdin", io.StringIO("Public call.\n")):
                    self.assertEqual(
                        cli.main(["run", "--session", self.session_dir]), 0
                    )
        self.assertEqual(seen, ["claude"])
        response_path = output.getvalue().strip()
        self.assertTrue(response_path.endswith("0002-peer-to-initiator.md"))
        with open(response_path, encoding="utf-8") as stream:
            self.assertIn("From: claude\nTo: sample-app\n", stream.read())

    def _turn_with_builder(self, build, body):
        return runner.run_turn(self.session_dir, body, 30.0, build)

    def test_invalid_body_and_timeout_fail_before_a_builder_or_publication(self):
        called = []

        def build(deadline, cwd):
            called.append(cwd)
            raise AssertionError("invalid input reached the connector")

        for body, timeout in (("   \n", 30.0), ("Body.\n", 0), ("Body.\n", float("nan"))):
            with self.subTest(body=body, timeout=timeout):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(self.session_dir, body, timeout, build)
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        self.assertEqual(called, [])
        self.assertEqual(self._messages(), [])

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
                    "note",
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
            self.assertIn("Record: note\n", text)
            self.assertIn("\n## Body\n\n", text)
            self.assertTrue(text.rstrip().endswith("."))
        self.assertEqual(self._leftover_temporaries(), [])

    def test_failure_during_publication_leaves_nothing_behind(self):
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
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
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        text = "# Message 0001\nRecord: note\nFrom: sample-app\n"
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

    def test_a_stop_after_the_rename_is_never_called_nothing_published(self):
        """The message is on the disk, so nothing may say it is not.

        A stop arriving in the instant after the rename - before control has
        even left the step that performed it - used to be turned into "nothing
        was published", which was the exact opposite of what had happened. The
        stop is now passed on as itself, and the message stays where it is.
        """
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        text = "# Message 0001\nRecord: note\nFrom: sample-app\n"
        real_replace = os.replace

        def replace_then_stop(source, destination):
            real_replace(source, destination)
            raise peer.SignalStop(signal.SIGTERM)

        with mock.patch("os.replace", replace_then_stop):
            with self.assertRaises(peer.SignalStop):
                session.publish(target, text)

        self.assertTrue(
            os.path.exists(target), "the published message is not there"
        )
        with open(target, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), text)
        self.assertEqual(self._leftover_temporaries(), [])

    def test_a_failure_after_the_rename_says_the_message_is_there(self):
        """Which side of the rename it happened on is decided by looking."""
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        text = "# Message 0001\nRecord: note\nFrom: sample-app\n"
        real_replace = os.replace

        def replace_then_fail(source, destination):
            real_replace(source, destination)
            raise OSError("forced failure after the rename")

        with mock.patch("os.replace", replace_then_fail):
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

    def test_a_temporary_file_that_merely_vanished_is_not_a_publication(self):
        """Gone is not the same as renamed, and only renamed is published.

        If the folder the message was being written in disappears, the rename
        fails and the temporary file goes with the folder. Deciding publication
        by the temporary file's absence would call that a published message and
        then say so about a file nobody can open. What is asked instead is
        whether the canonical name now holds the very file that was written.
        """
        directory = session.messages_dir(self.session_dir)
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        real_replace = os.replace

        def replace_after_the_folder_goes(source, destination):
            shutil.rmtree(directory)
            return real_replace(source, destination)

        with mock.patch("os.replace", replace_after_the_folder_goes):
            with self.assertRaises(BridgeError) as caught:
                session.publish(target, "# Message 0001\nnever arrives\n")

        self.assertEqual(caught.exception.failure, Failure.PUBLICATION_FAILURE)
        self.assertIn("nothing was published", str(caught.exception))
        self.assertFalse(
            os.path.exists(target),
            "a message that is not there was called published",
        )
        os.makedirs(directory)

    def test_a_temporary_file_that_will_not_go_is_reported_as_a_leftover(self):
        """A file left behind is what the person has to act on, so it wins.

        Publication fails, and clearing away the temporary file it had been
        writing into fails too. Reporting only that nothing was published would
        be true and useless: somebody now has a `.agent-bridge-publish-` file in
        their session folder that nothing has told them about. The failure has
        to be the one that names it.

        This check made the file the product could not remove, so this check
        removes it, and the leftover assertions afterwards stay meaningful.
        """
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        made = {}
        real_mkstemp = session.tempfile.mkstemp
        real_unlink = os.unlink

        def mkstemp(*args, **kwargs):
            handle, path = real_mkstemp(*args, **kwargs)
            if kwargs.get("prefix") == session.TEMP_PREFIX:
                made["path"] = path
            return handle, path

        def unlink(path, *args, **kwargs):
            if path == made.get("path"):
                raise OSError("forced removal failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(session.tempfile, "mkstemp", mkstemp):
            with mock.patch(
                "os.replace", side_effect=OSError("forced rename failure")
            ):
                with mock.patch("os.unlink", unlink):
                    with self.assertRaises(BridgeError) as caught:
                        session.publish(target, "# Message 0001\nno good\n")

        self.assertEqual(caught.exception.failure, Failure.CLEANUP_FAILURE)
        left = made["path"]
        rendered = str(caught.exception)
        self.assertIn(left, rendered)
        self.assertIn(target, rendered)
        self.assertIn("forced removal failure", rendered)
        self.assertIn("Next action:", rendered)
        self.assertFalse(
            os.path.exists(target), "nothing should have been published"
        )
        self.assertTrue(
            os.path.exists(left),
            "nothing was left behind, so this check proves nothing",
        )
        real_unlink(left)
        self.assertEqual(self._leftover_temporaries(), [])

    def test_a_rename_that_cannot_be_checked_is_not_called_nothing_published(
        self,
    ):
        """When there is no telling, saying there is would be the lie.

        The rename really happens, the filesystem then reports an ambiguous
        failure anyway, and the follow-up look at the canonical name fails too.
        The message is on the disk and complete - but nothing in this process
        can know that.

        Calling it `PUBLICATION_FAILURE` would tell the person nothing was
        published and to run the command again, which is exactly wrong: the
        message is there, and running again would write a second one. So the
        third answer gets its own failure, which names the file and asks them
        to go and look.
        """
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        text = "# Message 0001\nRecord: note\nFrom: sample-app\n"
        real_replace = os.replace
        real_stat = os.stat

        def replace_then_fail_ambiguously(source, destination):
            real_replace(source, destination)
            raise OSError("forced ambiguous input/output error")

        def stat_that_will_not_answer(path, *args, **kwargs):
            if path == target:
                raise OSError("forced failure looking at the canonical name")
            return real_stat(path, *args, **kwargs)

        with mock.patch("os.replace", replace_then_fail_ambiguously):
            with mock.patch("os.stat", stat_that_will_not_answer):
                with self.assertRaises(BridgeError) as caught:
                    session.publish(target, text)

        self.assertEqual(
            caught.exception.failure, Failure.PUBLICATION_UNCERTAIN
        )
        rendered = str(caught.exception)
        self.assertIn(target, rendered)
        self.assertNotIn("nothing was published", rendered)
        self.assertNotIn("then run the command again", rendered)
        self.assertIn("Do not run the command again", rendered)

        self.assertTrue(
            os.path.exists(target),
            "the message really was published, so this check proves nothing",
        )
        with open(target, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), text)
        self.assertEqual(self._leftover_temporaries(), [])

    def test_a_stop_before_the_rename_leaves_nothing_and_is_not_a_failure(self):
        """Stopped is stopped: nothing published, and nothing blamed on this."""
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        with mock.patch(
            "os.replace", side_effect=peer.SignalStop(signal.SIGHUP)
        ):
            with self.assertRaises(peer.SignalStop):
                session.publish(target, "# Message 0001\nnever arrives\n")

        self.assertFalse(os.path.exists(target))
        self.assertEqual(self._messages(), [])
        self.assertEqual(self._leftover_temporaries(), [])

    def test_a_stop_while_the_temporary_file_goes_still_removes_it(self):
        """Being stopped must not be a way to leave a temporary file behind.

        Publication fails before the rename, so the temporary file has to go,
        and the awkward moment is the removal itself. A stop raised where it
        landed would abandon `os.unlink` half done and leave a
        `.agent-bridge-publish-` file in the session folder with nothing saying
        it is there - which is the exact thing the report above it exists to
        prevent.

        The stop is delivered from inside the removal rather than aimed at it
        from outside, which is the only way to be sure it lands there, and it is
        an interrupt because that is how a person ordinarily stops a program.
        Stops are now written down for the length of the removal and raised once
        it is done, so the call still ends as the interrupt it was - and the
        file is gone. Before that change this same check leaves the file behind,
        which is what makes it discriminate rather than merely pass.
        """
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        real_unlink = os.unlink
        removed = []

        def unlink_after_a_stop(path, *args, **kwargs):
            name = os.path.basename(str(path))
            if name.startswith(session.TEMP_PREFIX) and not removed:
                removed.append(path)
                os.kill(os.getpid(), signal.SIGINT)
            return real_unlink(path, *args, **kwargs)

        with mock.patch(
            "os.replace", side_effect=OSError("forced rename failure")
        ):
            with mock.patch("os.unlink", unlink_after_a_stop):
                with self.assertRaises(KeyboardInterrupt):
                    session.publish(target, "# Message 0001\nnever arrives\n")

        self.assertEqual(
            len(removed), 1, "the temporary file was never put up for removal"
        )
        self.assertFalse(
            os.path.exists(removed[0]),
            "a stop during the removal left the temporary file on the disk",
        )
        self.assertFalse(
            os.path.exists(target), "nothing should have been published"
        )
        self.assertEqual(self._messages(), [])
        self.assertEqual(self._leftover_temporaries(), [])

    def test_a_stop_between_the_temporary_file_and_the_rename_leaves_nothing(
        self,
    ):
        """A healthy publication, stopped at its most awkward instant.

        Between the temporary file being made and the message being moved into
        place there is a stretch where the file exists under a name nothing has
        published. A real termination signal arriving there used to end the
        process outright: the handlers only went on once publication had already
        failed, so nothing caught the signal, none of the tidying ran, and a
        `.agent-bridge-publish-` file was left in the session folder with
        nothing to say it was there.

        Nothing is broken in this check. The publication is an ordinary healthy
        one, the signal is a real SIGTERM, and it is sent to a separate process
        so that this one survives sending it. The driver stands still in front
        of the rename so the signal cannot land anywhere but inside that
        stretch. The call must leave as the stop it was, and the session folder
        must be exactly as it was found.
        """
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        marker = os.path.join(self.temp, "ready-to-be-stopped.txt")
        driver = self._start_driver(PUBLISH_STOP_DRIVER, target, marker)

        self.assertTrue(
            _wait_for(lambda: os.path.exists(marker)),
            "the driver never reached the rename",
        )
        os.kill(driver.pid, signal.SIGTERM)
        driver.wait(timeout=SHORT_WAIT)

        self.assertEqual(
            driver.returncode,
            3,
            driver.stderr.read().decode("utf-8", "replace"),
        )
        self.assertFalse(
            os.path.exists(target), "nothing should have been published"
        )
        self.assertEqual(
            self._leftover_temporaries(),
            [],
            "a stop before the rename left the temporary file on the disk",
        )

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
            record.record(self.session_dir, "note", "Blocked.\n")
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
            self.session_dir, "note", "After the abrupt end.\n"
        )
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self._leftover_temporaries(), [])

    # -- failures publish no answer ----------------------------------------

    def test_timeout_publishes_no_response(self):
        self._expect(Failure.TIMEOUT, "hang", timeout=1.0)
        self.assertEqual(self._messages(), ["0001-initiator-to-peer.md"])
        self.assertEqual(self._leftover_temporaries(), [])

    def test_nonzero_exit_publishes_no_response(self):
        error = self._expect(Failure.PEER_FAILURE, "fail")
        self.assertIn("deliberate failure", str(error))
        self.assertEqual(self._messages(), ["0001-initiator-to-peer.md"])

    def test_no_output_at_all_publishes_no_response(self):
        self._expect(Failure.EMPTY_RESPONSE, "empty", body="Say nothing.\n")
        self.assertEqual(self._messages(), ["0001-initiator-to-peer.md"])

    def test_whitespace_only_output_publishes_no_response(self):
        self._expect(Failure.EMPTY_RESPONSE, "whitespace", body="Say air.\n")
        self.assertEqual(self._messages(), ["0001-initiator-to-peer.md"])

    # -- the body on the command line, where a harness has no other way ----

    def test_a_hyphen_led_body_travels_as_one_argument_and_comes_back_whole(
        self,
    ):
        body = (
            "---\ntitle: bound\n--mode yolo\n-p not an option\n"
            "--disallowed-tools=Edit is text\n---\n\nAnswer this.\n"
        )
        result = runner.run_turn(
            self.session_dir, body, 30.0, self._argument_builder("--prompt=")
        )
        with open(result.response_path, encoding="utf-8") as stream:
            response = stream.read()
        self.assertEqual(
            response,
            "# Message 0002\nFrom: claude\nTo: sample-app\n\n## Body\n\n"
            "--prompt=" + body,
        )
        self.assertNotIn("STDIN WAS NOT EMPTY", response)
        self.assertEqual(self._leftover_temporaries(), [])

    def test_a_body_the_command_line_cannot_carry_is_refused_before_anything(
        self,
    ):
        for name, body in (
            ("one byte over the limit", "x" * (COMMAND_LINE_BODY_LIMIT + 1)),
            ("a NUL byte inside", "before\x00after\n"),
        ):
            with self.subTest(body=name):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        self.session_dir,
                        body,
                        30.0,
                        self._argument_builder("--prompt="),
                    )
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertEqual(self._messages(), [])
                self.assertEqual(self._leftover_temporaries(), [])
        # And exactly at the limit the body is carried, not refused.
        result = runner.run_turn(
            self.session_dir,
            "y" * COMMAND_LINE_BODY_LIMIT,
            30.0,
            self._argument_builder(""),
        )
        self.assertEqual(result.response_sequence, 2)

    # -- the connector composes the command ---------------------------------

    def test_a_connector_precheck_runs_inside_the_turn_deadline(self):
        """A connector's own programs are bounded by the turn, not extra to it.

        Two shapes of the same rule. A precheck that hangs is stopped by the
        deadline it was handed, and a builder that comes back after the deadline
        has passed is refused before its command is ever run. Neither publishes
        anything at all, because nothing was ever sent.
        """
        for name, build in (
            ("a precheck that hangs", self._precheck_builder(30.0)),
            ("a builder that returns late", self._late_builder()),
        ):
            with self.subTest(builder=name):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        self.session_dir,
                        "Please answer this.\n",
                        2.0,
                        build,
                    )
                self.assertEqual(caught.exception.failure, Failure.TIMEOUT)
                self.assertEqual(self._messages(), [])
                self.assertEqual(self._leftover_temporaries(), [])

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

    def test_a_stop_while_the_child_is_starting_still_cleans_up(self):
        """The narrow window: the process exists, nothing yet owns it.

        Between the operating system making the child and this code reading
        which group it is in, there is a moment when a program is running and
        nothing has yet taken responsibility for ending it. A stop that raised
        there would leave that program behind.

        The signal is delivered from inside that moment rather than aimed at it
        from outside, which is the only way to be sure it lands there. The call
        must still leave by the stopped route, and the peer must be gone.

        All three stops are tried, and the keyboard interrupt is the one that
        matters most: Ctrl-C is how a person ordinarily stops a program, so a
        window that stayed open for it would be the window most often used.
        """
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=number):
                pid_path = os.path.join(
                    self.temp, "starting-{0}.txt".format(number)
                )
                driver = self._start_driver(
                    CREATION_DRIVER, "60", pid_path, str(int(number))
                )
                (peer_pid,) = self._await_reported(
                    pid_path, ("PEER",), running=False
                )

                driver.wait(timeout=SHORT_WAIT)
                self.assertEqual(
                    driver.returncode,
                    3,
                    driver.stderr.read().decode("utf-8", "replace"),
                )
                self.assertTrue(
                    _wait_for(lambda: _process_gone(peer_pid)),
                    "a stop while the child was starting left it running",
                )

    def test_a_second_stop_does_not_abandon_the_cleanup(self):
        """Being stopped twice must not leave more behind than being stopped.

        The first stop ends the waiting and starts the cleanup. The second
        arrives while that cleanup is under way - delivered from in front of it,
        so it really does land inside - and must not cut it short. Both the peer
        and the process it started have to be gone afterwards, exactly as they
        would be after one stop.
        """
        pid_path = os.path.join(self.temp, "second-stop-pids.txt")
        driver = self._start_driver(
            SECOND_STOP_DRIVER, "write-pids-then-hang", pid_path
        )
        peer_pid, child_pid = self._await_reported_pids(pid_path)

        os.kill(driver.pid, signal.SIGTERM)
        driver.wait(timeout=SHORT_WAIT)
        self.assertEqual(
            driver.returncode,
            3,
            driver.stderr.read().decode("utf-8", "replace"),
        )
        self.assertTrue(
            _wait_for(lambda: _process_gone(peer_pid)),
            "the second stop abandoned the cleanup and left the peer",
        )
        self.assertTrue(
            _wait_for(lambda: _process_gone(child_pid)),
            "the second stop abandoned the cleanup and left the child",
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


class ClaudeManagedSettingsReadiness(unittest.TestCase):
    """Claude reports managed-policy facts without reading or refusing them."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-claude-policy-")
        self.paths = (
            os.path.join(self.temp, "managed-settings.json"),
            os.path.join(self.temp, "managed-settings.d"),
            os.path.join(self.temp, "managed-mcp.json"),
            os.path.join(self.temp, "user.plist"),
            os.path.join(self.temp, "device.plist"),
        )
        self.sources = mock.patch.object(
            claude, "_managed_source_paths", return_value=self.paths
        )
        self.sources.start()

    def tearDown(self):
        self.sources.stop()
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_absent_or_empty_endpoint_sources_are_safe_without_reading_values(self):
        os.mkdir(self.paths[1])
        with open(os.path.join(self.paths[1], ".ignored.json"), "w") as stream:
            stream.write("not JSON and deliberately never opened")
        with open(os.path.join(self.paths[1], "README"), "w") as stream:
            stream.write("not a policy file")

        fact = claude._endpoint_managed_settings_fact()
        self.assertIn("no endpoint-managed", fact)
        self.assertIn("policy values were not opened", fact)

    def test_each_endpoint_policy_shape_is_reported_by_path_alone(self):
        for index, path in enumerate(self.paths):
            with self.subTest(path=path):
                shutil.rmtree(self.temp)
                os.mkdir(self.temp)
                if index == 1:
                    os.mkdir(path)
                    path = os.path.join(path, "10-hooks.json")
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write("a secret value this check must never parse")

                fact = claude._endpoint_managed_settings_fact()
                self.assertIn(path, fact)
                self.assertIn("policy values were not opened", fact)
                self.assertNotIn("a secret value", fact)

    def test_unreadable_endpoint_source_is_reported_as_unknown(self):
        denied = PermissionError(13, "permission denied", self.paths[0])
        with mock.patch.object(claude.os, "lstat", side_effect=denied):
            fact = claude._endpoint_managed_settings_fact()
        self.assertIn("inspection was inconclusive", fact)
        self.assertIn(self.paths[0], fact)
        self.assertIn("policy values were not opened", fact)

    def test_every_readable_doctor_state_is_reported_without_refusal(self):
        deadline = peer.Deadline(30.0)
        states = claude.REMOTE_STATUS_WITHOUT_POLICY + (
            "loaded",
            "fetch failed \u2014 using stale cache (network error)",
            "fetch failed \u2014 no policy applied (network error)",
        )
        for state in states:
            report = CompletedCall(
                0,
                "Claude Code doctor\n\n{0}{1}\n".format(
                    claude.REMOTE_STATUS_PREFIX, state
                ),
                "",
            )
            with self.subTest(state=state):
                with mock.patch.object(
                    claude.connectors, "probe", return_value=report
                ):
                    self.assertIn(
                        state,
                        claude._remote_managed_settings_fact(
                            "/path/to/claude", deadline, self.temp
                        ),
                    )

        with mock.patch.object(
            claude.connectors,
            "probe",
            return_value=CompletedCall(3, "", "doctor unavailable"),
        ):
            self.assertIn(
                "unknown",
                claude._remote_managed_settings_fact(
                    "/path/to/claude", deadline, self.temp
                ),
            )

    def test_readiness_warns_about_managed_policy_without_inventing_safe_mode(self):
        calls = (
            CompletedCall(0, "claude 2.1.251\n", ""),
            CompletedCall(
                0,
                '{"loggedIn": true, "authMethod": "claude.ai", '
                '"apiProvider": "firstParty"}',
                "",
            ),
            CompletedCall(
                0,
                claude.REMOTE_STATUS_PREFIX + "loaded\n",
                "",
            ),
            CompletedCall(0, " ".join(claude.QUALIFICATION.restrictions), ""),
        )
        with mock.patch.object(
            claude.connectors, "executable", return_value="/fake/claude"
        ), mock.patch.object(
            claude.connectors,
            "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            claude, "_endpoint_managed_settings_fact", return_value="endpoint fact"
        ), mock.patch.object(
            claude.connectors, "probe", side_effect=calls
        ):
            result = claude.check(peer.Deadline(30.0), self.temp)

        self.assertIsInstance(result, connectors.CheckResult)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("endpoint fact", result.warnings[0])
        self.assertIn("loaded", result.warnings[0])
        self.assertIn("--restricted", result.warnings[0])
        self.assertNotIn("--safe-mode", result.warnings[0])


class SixTargetConnectorBehavior(unittest.TestCase):
    """The six literal targets, warning model, and new courier mechanics."""

    TARGETS = ("codex", "claude", "zcode", "hermes", "minimax", "qwen")

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-connectors-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_exact_targets_resolve_with_only_the_selected_module_imported(self):
        self.assertEqual(connectors.HARNESS_IDS, self.TARGETS)
        source = (
            "import sys\n"
            "from bridge import connectors\n"
            "selected = sys.argv[1]\n"
            "connector = connectors.resolve(selected)\n"
            "targets = set(connectors.HARNESS_IDS)\n"
            "loaded = sorted(name.rsplit('.', 1)[1] for name in sys.modules "
            "if name.startswith('bridge.') and name.rsplit('.', 1)[1] in targets)\n"
            "print(connector.HARNESS_ID + ':' + ','.join(loaded))\n"
        )
        for selected in self.TARGETS:
            with self.subTest(selected=selected):
                completed = subprocess.run(
                    (sys.executable, "-c", source, selected),
                    cwd=REPO_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    timeout=SHORT_WAIT,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(
                    completed.stdout.strip(),
                    "{0}:{0}".format(selected),
                )

        with self.assertRaises(BridgeError) as caught:
            connectors.resolve("other")
        self.assertEqual(caught.exception.failure, Failure.UNKNOWN_HARNESS)

    def test_check_and_run_warnings_are_nonblocking_and_precede_publication(self):
        warnings = ("first concrete boundary", "second concrete boundary")

        class WarnedConnector:
            @staticmethod
            def check(deadline, cwd):
                return connectors.CheckResult("warned target is ready", warnings)

            @staticmethod
            def build_command(deadline, cwd):
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "plain"),
                    cwd=cwd,
                    env=tuple(os.environ.items()),
                    warnings=warnings,
                )

        checked_out = io.StringIO()
        checked_err = io.StringIO()
        with mock.patch.object(connectors, "resolve", return_value=WarnedConnector):
            with mock.patch("sys.stdout", checked_out), mock.patch(
                "sys.stderr", checked_err
            ):
                self.assertEqual(cli.main(["check", "--peer", "codex"]), 0)
        self.assertEqual(
            checked_out.getvalue().splitlines(),
            [
                "warned target is ready",
                "Warning: first concrete boundary",
                "Warning: second concrete boundary",
            ],
        )
        self.assertEqual(checked_err.getvalue(), "")

        session_dir = os.path.join(self.temp, "warned-run")
        record.record(
            session_dir,
            "session-create",
            "Warning timing.\n",
            initiator="ordinary.app",
            peer="codex",
        )
        events = []
        real_publish = runner.session_module.publish

        class ObservedErrors(io.StringIO):
            def write(inner_self, text):
                if text:
                    events.append(("warning", text))
                return super().write(text)

        def observed_publish(path, text):
            events.append(("publish", os.path.basename(path)))
            return real_publish(path, text)

        run_out = io.StringIO()
        run_err = ObservedErrors()
        with mock.patch.object(connectors, "resolve", return_value=WarnedConnector):
            with mock.patch.object(
                runner.session_module, "publish", side_effect=observed_publish
            ):
                with mock.patch("sys.stdout", run_out), mock.patch(
                    "sys.stderr", run_err
                ), mock.patch("sys.stdin", io.StringIO("A warned request.\n")):
                    self.assertEqual(
                        cli.main(["run", "--session", session_dir]),
                        0,
                    )

        self.assertEqual(
            events[:3],
            [
                ("warning", "Warning: first concrete boundary\n"),
                ("warning", "Warning: second concrete boundary\n"),
                ("publish", "0001-initiator-to-peer.md"),
            ],
        )
        self.assertEqual(events[3], ("publish", "0002-peer-to-initiator.md"))
        self.assertTrue(run_out.getvalue().strip().endswith("0002-peer-to-initiator.md"))

    def test_missing_software_authentication_and_mechanics_remain_failures(self):
        with mock.patch.object(connectors.shutil, "which", return_value=None):
            with self.assertRaises(BridgeError) as caught:
                connectors.executable("missing-peer")
        self.assertEqual(caught.exception.failure, Failure.MISSING_CLI)

        authentication_checks = (
            (
                "claude",
                lambda: claude._signed_in(
                    CompletedCall(0, '{"loggedIn": false}', "")
                ),
            ),
            (
                "hermes",
                lambda: hermes._signed_in(
                    CompletedCall(0, "not logged in\n", "")
                ),
            ),
        )
        for name, check in authentication_checks:
            with self.subTest(authentication=name):
                with self.assertRaises(BridgeError) as caught:
                    check()
                self.assertEqual(
                    caught.exception.failure, Failure.AUTHENTICATION_REQUIRED
                )

        with mock.patch.object(zcode, "_modified", return_value=None):
            with self.assertRaises(BridgeError) as caught:
                zcode._sign_in_facts()
        self.assertEqual(caught.exception.failure, Failure.AUTHENTICATION_REQUIRED)

        codex_calls = (
            CompletedCall(0, "codex-cli 0.147.0\n", ""),
            CompletedCall(1, "", "not logged in"),
        )
        with mock.patch.object(
            codex.connectors, "executable", return_value="/fake/codex"
        ), mock.patch.object(
            codex.connectors,
            "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(codex.connectors, "probe", side_effect=codex_calls):
            with self.assertRaises(BridgeError) as caught:
                codex.check(peer.Deadline(30.0), self.temp)
        self.assertEqual(caught.exception.failure, Failure.AUTHENTICATION_REQUIRED)

        qualification = connectors.Qualification(
            "fake-peer",
            ("1.2.3",),
            "Darwin",
            ("26",),
            ("arm64",),
            ("--required",),
        )
        with self.assertRaises(BridgeError) as caught:
            connectors.qualified_version("no readable release", qualification, [])
        self.assertEqual(caught.exception.failure, Failure.UNREPORTABLE_VERSION)

        for help_call in (
            CompletedCall(3, "", "help failed"),
            CompletedCall(0, "--some-other-switch", ""),
        ):
            with self.subTest(help=help_call):
                with self.assertRaises(BridgeError) as caught:
                    connectors.qualified_restrictions(help_call, qualification)
                self.assertEqual(
                    caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE
                )

    def test_existing_connectors_warn_about_surviving_configuration(self):
        for phrase in (
            "$CODEX_HOME/config.toml",
            "Trusted-project .codex/config.toml",
            "project hooks or rules",
            "system configuration",
            "managed_config.toml",
            "requirements.toml",
            "cloud-delivered requirements",
            "macOS MDM",
            "user/global hooks or rules",
            "managed defaults",
            "MCP",
            "telemetry",
        ):
            self.assertIn(phrase, codex.WARNING)

        plugin_listing = CompletedCall(
            0,
            '{"plugins":[{"id":"exposed","enabled":true,'
            '"declaredMcpServerNames":["route"],"hookDetails":[{}]}]}',
            "",
        )
        with mock.patch.object(
            zcode.connectors, "probe", return_value=plugin_listing
        ):
            plugin_fact = zcode._plugin_fact(
                "/fake/node", "/fake/zcode", peer.Deadline(30.0), self.temp
            )
        self.assertIn("exposed", plugin_fact)
        self.assertIn("MCP server route", plugin_fact)
        self.assertIn("hook", plugin_fact)

        with mock.patch.object(zcode, "_modified", side_effect=(100.0, 50.0)):
            sign_in_fact = zcode._sign_in_facts()
        self.assertIn("sign-in itself is not confirmed", sign_in_fact)

        for phrase in (
            "courier-only",
            "user memory can remain",
            "one --oneshot argument",
            "visible to other processes",
        ):
            self.assertIn(phrase, hermes.WARNING)

    def test_readiness_distinguishes_confirmed_and_unconfirmed_authentication(self):
        confirmed = (
            (
                codex,
                ("/fake/codex", "0.147.0", "Darwin 26 arm64", ()),
            ),
            (
                claude,
                (
                    "/fake/claude",
                    "2.1.251",
                    "Darwin 26 arm64",
                    "signed in through claude.ai",
                    (),
                ),
            ),
            (
                hermes,
                (
                    "/fake/hermes",
                    "0.18.2",
                    "Darwin 26 arm64",
                    "signed in to the Nous Portal",
                    (),
                ),
            ),
        )
        for connector, prerequisite in confirmed:
            with self.subTest(confirmed=connector.HARNESS_ID), mock.patch.object(
                connector, "_prerequisites", return_value=prerequisite
            ):
                checked = connector.check(peer.Deadline(30.0), self.temp)
                self.assertTrue(
                    checked.message.startswith(
                        "{0} is ready:".format(connector.HARNESS_ID)
                    ),
                    checked.message,
                )
                self.assertNotIn("authentication is unconfirmed", checked.message)

        unconfirmed = (
            (zcode, "minimum local configuration is present"),
            (minimax, "no state-free status command"),
            (qwen, "no safe status command"),
        )
        for connector, authentication in unconfirmed:
            prerequisite = (
                "/fake/{0}".format(connector.HARNESS_ID),
                "1.2.3",
                "Darwin 26 arm64",
                authentication,
                (),
            )
            with self.subTest(unconfirmed=connector.HARNESS_ID), mock.patch.object(
                connector, "_prerequisites", return_value=prerequisite
            ):
                checked = connector.check(peer.Deadline(30.0), self.temp)
                self.assertTrue(
                    checked.message.startswith(
                        "{0} mechanics are ready, but live authentication is "
                        "unconfirmed:".format(connector.HARNESS_ID)
                    ),
                    checked.message,
                )
                self.assertIn(authentication, checked.message)

    def test_minimax_uses_only_its_two_safe_checks_and_exact_courier_vector(self):
        probed = []

        def probe(argv, cwd, deadline, env=None):
            probed.append(tuple(argv))
            if tuple(argv)[-1] == "--version":
                return CompletedCall(0, "mcode 0.2.8\n", "")
            return CompletedCall(
                0,
                " ".join(minimax.QUALIFICATION.restrictions),
                "",
            )

        with mock.patch.object(
            minimax.connectors, "executable", return_value="/fake/mcode"
        ), mock.patch.object(minimax.connectors, "probe", side_effect=probe), mock.patch.object(
            connectors.platform, "system", return_value="Linux"
        ), mock.patch.object(
            connectors.platform, "mac_ver", return_value=("", ("", "", ""), "")
        ), mock.patch.object(
            connectors.platform, "release", return_value="6.8.0"
        ), mock.patch.object(
            connectors.platform, "machine", return_value="x86_64"
        ):
            checked = minimax.check(peer.Deadline(30.0), self.temp)

        self.assertIn(
            "minimax mechanics are ready, but live authentication is "
            "unconfirmed: version 0.2.8",
            checked.message,
        )
        self.assertEqual(
            probed,
            [
                ("/fake/mcode", "--version"),
                ("/fake/mcode", "exec", "--help"),
            ],
        )
        self.assertTrue(all("provider" not in " ".join(call) for call in probed))
        self.assertGreaterEqual(
            sum("outside the release evidence" in warning for warning in checked.warnings),
            2,
        )
        self.assertTrue(
            any("authentication" in warning.lower() for warning in checked.warnings)
        )
        for phrase in (
            "discretionary permission mode",
            "not a sandbox",
            "--max-steps=1 limits assistant steps",
            "does not disable tools",
            "ask is interactive",
            "full bypasses",
            "off disables permission checks",
        ):
            self.assertIn(phrase, minimax.WARNING)

        prerequisite = (
            "/fake/mcode",
            "0.2.7",
            "Darwin 26 arm64",
            "authentication not confirmed",
            (minimax.WARNING,),
        )
        with mock.patch.object(minimax, "_prerequisites", return_value=prerequisite):
            command = minimax.build_command(peer.Deadline(30.0), self.temp)
            huge = minimax.build_command(
                peer.Deadline(
                    minimax.MAX_NATIVE_TIMEOUT_MILLISECONDS / 1000.0 + 1.0
                ),
                self.temp,
            )
            astronomical = minimax.build_command(
                peer.Deadline(1e308), self.temp
            )
        self.assertEqual(
            command.argv,
            (
                "/fake/mcode",
                "exec",
                "--input",
                "-",
                "--input-format",
                "text",
                "--cwd",
                self.temp,
                "--permission",
                "smart",
                "--timeout",
                "30000ms",
                "--max-steps",
                "1",
                "--output-format",
                "text",
            ),
        )
        self.assertIsNone(command.body_argument)
        self.assertEqual(command.warnings, (minimax.WARNING,))
        self.assertFalse(
            any(value in command.argv for value in ("ask", "full", "off"))
        )
        self.assertNotIn("--timeout", huge.argv)
        self.assertNotIn("--timeout", astronomical.argv)
        self.assertIn("--max-steps", huge.argv)

    def _qwen_prerequisite(self):
        return (
            "/fake/qwen",
            "0.23.0",
            "Darwin 26 arm64",
            "authentication not confirmed",
            (qwen.INPUT_WARNING, qwen.BOUNDARY_WARNING),
        )

    def test_qwen_surfaces_the_input_exception_and_exact_zero_tool_vector(self):
        with mock.patch.object(
            qwen, "_prerequisites", return_value=self._qwen_prerequisite()
        ), mock.patch.dict(
            os.environ,
            {
                "QWEN_SANDBOX": "false",
                "SANDBOX": "already-inside",
                "SEATBELT_PROFILE": "permissive-open",
                "QWEN_SANDBOX_PROXY_COMMAND": "detached proxy command",
                "NO_BROWSER": "0",
                "HTTPS_PROXY": "http://provider-proxy.invalid",
            },
        ):
            checked = qwen.check(peer.Deadline(30.0), self.temp)
            commands = {
                seconds: qwen.build_command(peer.Deadline(seconds), self.temp)
                for seconds in (
                    45.0,
                    900.0,
                    12.25,
                    qwen.MAX_NATIVE_WALL_TIME_SECONDS + 1.0,
                    1e308,
                )
            }

        command = commands[900.0]

        self.assertEqual(
            checked.warnings,
            (qwen.INPUT_WARNING, qwen.BOUNDARY_WARNING),
        )
        self.assertTrue(
            checked.message.startswith(
                "qwen mechanics are ready, but live authentication is unconfirmed:"
            )
        )
        self.assertEqual(command.warnings, checked.warnings)
        for phrase in (
            "leading / commands",
            "unescaped @ references",
            "both text and stream-json",
            "alter or replace the effective prompt",
            "inject readable file or resource content",
            "complete a command without a model call",
            "Safe mode does not disable",
            "no lossless raw escape or switch",
            "before --max-tool-calls=0",
            "budget does not stop it",
            "pre-model command families",
            "/bug, /config, /update, /import-config, /language, /effort, "
            "/model, and /doctor",
            "write diagnostics",
            "installer rollback",
            "Other recognized slash-command preprocessing remains enabled",
        ):
            self.assertIn(phrase, qwen.INPUT_WARNING)
        self.assertIn("first such attempt", qwen.BOUNDARY_WARNING)
        self.assertIn("bundled skills still load", qwen.BOUNDARY_WARNING)
        self.assertNotIn("extensions, skills", qwen.BOUNDARY_WARNING)
        self.assertEqual(
            command.argv,
            (
                "/fake/qwen",
                "--safe-mode",
                "--sandbox=sandbox-exec",
                "--chat-recording=false",
                "--approval-mode=plan",
                "--disabled-slash-commands="
                "bug,config,update,import-config,language,effort,model,doctor",
                "--max-tool-calls=0",
                "--max-session-turns=1",
                "--max-wall-time=900s",
                "--input-format=text",
                "--output-format=json",
                "--openai-logging=false",
            ),
        )
        self.assertIn("--max-wall-time=45s", commands[45.0].argv)
        self.assertIn("--max-wall-time=900s", commands[900.0].argv)
        self.assertIn("--max-wall-time=13s", commands[12.25].argv)
        self.assertFalse(
            any(
                argument.startswith("--max-wall-time")
                for argument in commands[
                    qwen.MAX_NATIVE_WALL_TIME_SECONDS + 1.0
                ].argv
            )
        )
        self.assertFalse(
            any(
                argument.startswith("--max-wall-time")
                for argument in commands[1e308].argv
            )
        )
        self.assertIsNone(command.body_argument)
        self.assertIs(command.response_parser, qwen.parse_response)
        self.assertEqual(command.stdin_body_limit, 8 * 1024 * 1024)
        environment = dict(command.env)
        self.assertEqual(
            environment["QWEN_RUNTIME_DIR"],
            os.path.join(self.temp, qwen.RUNTIME_DIRECTORY),
        )
        self.assertEqual(environment["QWEN_TELEMETRY_ENABLED"], "0")
        self.assertEqual(environment["QWEN_USAGE_STATISTICS_ENABLED"], "0")
        self.assertEqual(environment["NODE_DISABLE_COMPILE_CACHE"], "1")
        self.assertEqual(environment["NO_BROWSER"], "1")
        self.assertEqual(environment["SEATBELT_PROFILE"], "restrictive-open")
        self.assertEqual(
            environment["HTTPS_PROXY"], "http://provider-proxy.invalid"
        )
        for name in (
            "QWEN_SANDBOX",
            "SANDBOX",
            "QWEN_SANDBOX_PROXY_COMMAND",
        ):
            self.assertNotIn(name, environment)
        self.assertFalse(
            any(
                argument.startswith("--model")
                or argument.startswith("--provider")
                for argument in command.argv
            )
        )

    def test_qwen_accepts_only_a_successful_terminal_json_result(self):
        self.assertEqual(
            qwen.parse_response(
                '[{"type":"assistant","message":"ignored"},'
                '{"type":"result","subtype":"success","is_error":false,'
                '"result":"Final caf\\u00e9\\n"}]'
            ),
            "Final caf\u00e9\n",
        )
        invalid = (
            "not JSON",
            "[]",
            '[{"type":"assistant","message":"not terminal result"}]',
            '[{"type":"result","subtype":"error","is_error":true,'
            '"error":{"message":"failed"}}]',
            '[{"type":"result","subtype":"success","is_error":false}]',
        )
        for output in invalid:
            with self.subTest(output=output):
                with self.assertRaises(BridgeError) as caught:
                    qwen.parse_response(output)
                self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)

    def test_qwen_oversized_standard_input_is_refused_before_warning_or_request(self):
        session_dir = os.path.join(self.temp, "qwen-oversized")
        record.record(
            session_dir,
            "session-create",
            "Qwen input bound.\n",
            initiator="ordinary.app",
            peer="qwen",
        )
        warned = []
        with mock.patch.object(
            qwen, "_prerequisites", return_value=self._qwen_prerequisite()
        ):
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(
                    session_dir,
                    "x" * (qwen.STDIN_BODY_LIMIT + 1),
                    30.0,
                    build_command=qwen.build_command,
                    warning_writer=warned.append,
                )
        self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        self.assertIn("silently truncates", str(caught.exception))
        self.assertIn(str(qwen.STDIN_BODY_LIMIT), str(caught.exception))
        self.assertEqual(warned, [])
        self.assertEqual(os.listdir(session.messages_dir(session_dir)), [])

    def test_qualification_prompts_are_local_only_and_qwen_claims_no_raw_input(self):
        token = "abc123"
        evidence = "PROJECT_READ_CANARY={0}".format(token)
        outside = "/a/path-that-must-not-be-supplied"

        for target in ("codex", "claude", "zcode"):
            with self.subTest(project_target=target):
                body = release_conformance._qualification_body(
                    target, outside, token, evidence
                )
                self.assertIn("Inside the supplied disposable repository", body)
                self.assertIn("Do not attempt any browser or web access", body)
                self.assertNotIn(outside, body)

        for target in ("hermes", "minimax"):
            with self.subTest(courier_target=target):
                body = release_conformance._qualification_body(
                    target, outside, token, evidence
                )
                self.assertIn("Do not try to read a project", body)
                self.assertIn("Make no file, shell, Git", body)
                self.assertNotIn(outside, body)

        qwen_body = release_conformance._qualification_body(
            "qwen", outside, token, evidence
        )
        self.assertNotIn("/", qwen_body)
        self.assertNotIn("@", qwen_body)
        self.assertNotIn("raw", qwen_body.lower())
        self.assertNotIn("TRANSPORT_ECHO", qwen_body)
        self.assertNotIn("LEADING_HYPHEN_ECHO", qwen_body)
        self.assertIn("Do not try any tool", qwen_body)
        self.assertNotIn(outside, qwen_body)

        response = "response-qwen-{0}".format(token)
        response_text = session.peer_to_initiator_text(
            2,
            "qwen",
            "release-qualification",
            "QUALIFICATION_RESPONSE: {0}\nCOMPLETE: {0}\n".format(response),
        )
        self.assertEqual(
            release_conformance._check_response(
                "qwen", response_text, token, evidence
            ),
            {},
        )


class QualificationUsesProductionController(unittest.TestCase):
    """Qualification reuses the public CLI and its one production process owner."""

    def test_qualification_combines_original_failure_with_final_inspection(self):
        captured = {}
        real_synthetic = release_conformance._synthetic_repository

        def synthetic(parent, token):
            project, evidence = real_synthetic(parent, token)
            captured["parent"] = parent
            captured["project"] = project
            return project, evidence

        def create_session(session_dir, peer_id, body, project=None):
            record.record(
                session_dir,
                "session-create",
                body,
                initiator="release-qualification",
                peer=peer_id,
                project=project,
            )

        def fail_after_mutation(arguments, body):
            with open(
                os.path.join(captured["project"], "unexpected.txt"),
                "w",
                encoding="utf-8",
            ) as stream:
                stream.write("mutation that final inspection must report\n")
            with open(
                os.path.join(captured["project"], ".git", "index.lock"),
                "w",
                encoding="utf-8",
            ) as stream:
                stream.write("stale lock\n")
            raise release_conformance.ConformanceError("primary launcher failure")

        output = io.StringIO()
        with mock.patch.object(
            release_conformance,
            "_synthetic_repository",
            side_effect=synthetic,
        ), mock.patch.object(
            release_conformance,
            "_restriction_vector",
            return_value="fixed vector",
        ), mock.patch.object(
            release_conformance,
            "_create_session",
            side_effect=create_session,
        ), mock.patch.object(
            release_conformance,
            "_public_bridge",
            side_effect=fail_after_mutation,
        ), mock.patch.object(
            release_conformance,
            "_inspect_post_call_state",
            wraps=release_conformance._inspect_post_call_state,
        ) as inspected, contextlib.redirect_stdout(output):
            with self.assertRaises(
                release_conformance.ConformanceError
            ) as caught:
                release_conformance.qualify("codex")

        failure = str(caught.exception)
        self.assertIn("original failure: primary launcher failure", failure)
        self.assertIn("repository untracked changed", failure)
        self.assertIn("synthetic repository is not clean", failure)
        self.assertIn("Git lock files survived", failure)
        self.assertEqual(inspected.call_count, 1)
        self.assertIn("production bounded process-group controller", output.getvalue())
        self.assertFalse(os.path.exists(captured["parent"]))


class FormatTwoRecords(unittest.TestCase):
    """The immutable session, neutral note, strict reader, and closed kinds."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-records-")
        self.session_dir = os.path.join(self.temp, "session")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _create(self, project=None, initiator="my-app", peer_id="claude"):
        return record.record(
            self.session_dir,
            "session-create",
            "A bounded courier conversation.\n",
            initiator=initiator,
            peer=peer_id,
            project=project,
        )

    def _read(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()

    def _numbered(self, sequence):
        return self._read(
            session.message_path(
                self.session_dir, sequence, session.INITIATOR_RECORD_SUFFIX
            )
        )

    def _message_names(self):
        return sorted(os.listdir(session.messages_dir(self.session_dir)))

    def test_session_create_writes_the_session_file_and_no_message(self):
        """The one kind that takes no number, because it is not a message."""
        path = self._create(project=self.temp)

        self.assertEqual(path, session.session_file(self.session_dir))
        self.assertEqual(
            self._read(path),
            "# Session\n\nBridge-Format: 2\nInitiator: my-app\nPeer: claude\n"
            "Project: {0}\n\n## Body\n\n"
            "A bounded courier conversation.\n".format(self.temp),
        )
        self.assertEqual(self._message_names(), [])

        with self.assertRaises(BridgeError) as caught:
            self._create()
        self.assertEqual(caught.exception.failure, Failure.SESSION_EXISTS)

    def test_public_record_commands_create_the_session_and_one_note(self):
        public_session = os.path.join(self.temp, "public-session")
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            with mock.patch("sys.stdin", io.StringIO("Session body.\n")):
                self.assertEqual(
                    cli.main(
                        [
                            "record",
                            "--session",
                            public_session,
                            "--kind",
                            "session-create",
                            "--initiator",
                            "ordinary.app",
                            "--peer",
                            "codex",
                        ]
                    ),
                    0,
                )
            with mock.patch("sys.stdin", io.StringIO("Neutral note.\n")):
                self.assertEqual(
                    cli.main(
                        [
                            "record",
                            "--session",
                            public_session,
                            "--kind",
                            "note",
                        ]
                    ),
                    0,
                )
        written = output.getvalue().splitlines()
        self.assertEqual(written[0], session.session_file(public_session))
        self.assertEqual(
            os.path.basename(written[1]), "0001-initiator-record.md"
        )

    def test_a_note_is_one_numbered_initiator_record_with_an_inert_body(self):
        self._create()
        body = "From: forged\nPeer: codex\nProject: /tmp\n\nStill body text.\n"
        path = record.record(
            self.session_dir, "note", body
        )

        self.assertEqual(os.path.basename(path), "0001-initiator-record.md")
        self.assertEqual(
            self._read(path),
            "# Message 0001\nRecord: note\nFrom: my-app\n\n"
            "## Body\n\n" + body,
        )
        parsed = session.read_session(self.session_dir)
        self.assertEqual(parsed.initiator, "my-app")
        self.assertEqual(parsed.peer, "claude")
        self.assertIsNone(parsed.project)

    def test_record_bodies_preserve_the_callers_final_newline_choice(self):
        self._create()
        path = record.record(self.session_dir, "note", "No final newline")
        self.assertEqual(
            self._read(path),
            "# Message 0001\nRecord: note\nFrom: my-app\n\n"
            "## Body\n\nNo final newline",
        )

    def test_unknown_kind_is_refused_and_writes_nothing(self):
        self._create()

        with self.assertRaises(BridgeError) as caught:
            record.record(
                self.session_dir,
                "legacy-kind",
                "Anything at all.\n",
            )

        self.assertEqual(
            caught.exception.failure, Failure.UNKNOWN_RECORD_KIND
        )
        self.assertIn("legacy-kind", str(caught.exception))
        self.assertEqual(self._message_names(), [])

    def test_unknown_target_is_refused_before_a_session_is_published(self):
        with self.assertRaises(BridgeError) as caught:
            self._create(peer_id="other")
        self.assertEqual(caught.exception.failure, Failure.UNKNOWN_HARNESS)
        self.assertFalse(os.path.exists(session.session_file(self.session_dir)))

    def test_valid_unregistered_labels_work_and_invalid_labels_publish_nothing(self):
        labels = ("ora", "gear-3", "vibe_coder.2", "codex")
        for index, label in enumerate(labels):
            with self.subTest(label=label):
                self.session_dir = os.path.join(self.temp, "session-{0}".format(index))
                self._create(initiator=label)
                self.assertEqual(session.read_session(self.session_dir).initiator, label)
        for index, label in enumerate(("", "-bad", "bad label", "caf\u00e9", "bad/label")):
            with self.subTest(label=label):
                self.session_dir = os.path.join(self.temp, "invalid-{0}".format(index))
                with self.assertRaises(BridgeError) as caught:
                    self._create(initiator=label)
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertFalse(os.path.exists(session.session_file(self.session_dir)))

    def test_project_must_be_absolute_and_exist_when_the_session_is_created(self):
        spaced = os.path.join(self.temp, "spaced ")
        lined = os.path.join(self.temp, "line\nbreak")
        os.makedirs(spaced)
        os.makedirs(lined)
        for index, project in enumerate((
            "relative",
            os.path.join(self.temp, "missing"),
            spaced,
            lined,
        )):
            with self.subTest(project=project):
                self.session_dir = os.path.join(self.temp, "project-{0}".format(index))
                with self.assertRaises(BridgeError) as caught:
                    self._create(project=project)
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertFalse(os.path.exists(session.session_file(self.session_dir)))

    def test_strict_reader_rejects_legacy_duplicate_unknown_and_malformed_sessions(self):
        variants = {
            "legacy": (
                "# Session\n\nBridge-Format: 1\nLocal: codex\nPeer: claude\n"
                "Workflow: planning\n\n## Body\n\nOld shape.\n"
            ),
            "duplicate": (
                "# Session\n\nBridge-Format: 2\nInitiator: app\nInitiator: other\n"
                "Peer: claude\n\n## Body\n\nDuplicate.\n"
            ),
            "unknown": (
                "# Session\n\nBridge-Format: 2\nInitiator: app\nPeer: claude\n"
                "Status: ready\n\n## Body\n\nUnknown.\n"
            ),
            "bad target": (
                "# Session\n\nBridge-Format: 2\nInitiator: app\nPeer: other\n\n"
                "## Body\n\nBad target.\n"
            ),
            "bad project": (
                "# Session\n\nBridge-Format: 2\nInitiator: app\nPeer: claude\n"
                "Project: relative\n\n## Body\n\nBad project.\n"
            ),
            "empty body": (
                "# Session\n\nBridge-Format: 2\nInitiator: app\nPeer: claude\n\n"
                "## Body\n\n   \n"
            ),
        }
        for index, (name, text) in enumerate(variants.items()):
            with self.subTest(case=name):
                self.session_dir = os.path.join(self.temp, "invalid-read-{0}".format(index))
                os.makedirs(session.messages_dir(self.session_dir))
                with open(session.session_file(self.session_dir), "w", encoding="utf-8") as stream:
                    stream.write(text)
                with self.assertRaises(BridgeError) as caught:
                    session.read_session(self.session_dir)
                self.assertEqual(caught.exception.failure, Failure.SESSION_INVALID)

    def test_invalid_utf8_session_is_reported_as_session_invalid_by_the_cli(self):
        os.makedirs(session.messages_dir(self.session_dir))
        with open(session.session_file(self.session_dir), "wb") as stream:
            stream.write(b"# Session\n\nBridge-Format: 2\nInitiator: app\xff\n")

        with self.assertRaises(BridgeError) as caught:
            session.read_session(self.session_dir)
        self.assertEqual(caught.exception.failure, Failure.SESSION_INVALID)

        captured = io.StringIO()
        with mock.patch("sys.stderr", captured):
            with mock.patch("sys.stdin", io.StringIO("Neutral note.\n")):
                status = cli.main(
                    [
                        "record",
                        "--session",
                        self.session_dir,
                        "--kind",
                        "note",
                    ]
                )

        self.assertEqual(status, 1)
        self.assertIn(
            "record could not be read as a valid session", captured.getvalue()
        )
        self.assertIn("Next action:", captured.getvalue())
        self.assertNotIn("Traceback", captured.getvalue())
        self.assertEqual(self._message_names(), [])

    def test_note_refuses_session_creation_arguments(self):
        self._create()
        for kwargs in ({"initiator": "other"}, {"peer": "codex"}, {"project": self.temp}):
            with self.subTest(arguments=kwargs):
                with self.assertRaises(BridgeError) as caught:
                    record.record(self.session_dir, "note", "Neutral.\n", **kwargs)
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        self.assertEqual(self._message_names(), [])


class CommandLineBody(unittest.TestCase):
    """The body as a final argument: whole, alone, bounded, and never a project.

    Standard input is the transport wherever a program has one. For the two
    harnesses whose programs have none, the connector says so on the command
    it returns and the runner hands the body over as exactly one final argument
    instead. Everything the fake peer can see about that is checked here: what
    it received as its final argument and that its standard input stayed empty,
    that a body over the limit never becomes a request at all, and that a
    courier-only peer is refused a project by the command line before the
    connector's own program is even looked for.
    """

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-argv-")
        self.session_dir = os.path.join(self.temp, "session")
        record.record(
            self.session_dir,
            "session-create",
            "Prove the command-line transport without a real harness.\n",
            initiator="sample-app",
            peer="hermes",
        )

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _messages(self):
        return sorted(os.listdir(session.messages_dir(self.session_dir)))

    def _builder(self, prefix, *vector_tail):
        """A connector whose program takes the body on its command line.

        `prefix` is what the connector puts in front of the body - empty when
        the vector ends in a bare `--`, the option's own attached form when the
        program's parser needs that - and `vector_tail` is the end of the
        vector itself.
        """
        argv = (sys.executable, FAKE_PEER, "last-argument") + vector_tail

        def build(deadline, cwd):
            return PeerCommand(
                argv=argv,
                cwd=cwd,
                env=tuple(os.environ.items()),
                body_argument=prefix,
            )

        return build

    def test_the_body_is_one_final_argument_and_standard_input_stays_empty(
        self,
    ):
        """A body that looks like options arrives whole, and only once."""
        body = "---\nname: probe\n---\n-h\n--oneshot=not an option\n"
        for name, prefix, tail in (
            ("a bare -- and then the body", "", ("--",)),
            ("the option's attached value", "--oneshot=", ()),
        ):
            with self.subTest(transport=name):
                result = runner.run_turn(
                    self.session_dir,
                    body,
                    30.0,
                    self._builder(prefix, *tail),
                )
                with open(result.response_path, encoding="utf-8") as stream:
                    response = stream.read()
                self.assertEqual(
                    response,
                    "# Message {0:04d}\nFrom: hermes\nTo: sample-app\n\n## Body\n\n"
                    "{1}".format(result.response_sequence, prefix + body),
                )
                self.assertNotIn("STANDARD INPUT WAS NOT EMPTY", response)

    def test_a_body_too_big_for_the_command_line_is_refused_unsent(self):
        """Too big is measured in encoded bytes, and refused before sending.

        The limit is a byte count, not a character count, so a body of
        two-byte characters reaches it with half as many characters. A NUL
        byte is refused for a different reason: no argument can carry one.
        """
        limit = COMMAND_LINE_BODY_LIMIT
        for name, body, expected in (
            ("one byte over, in one-byte characters", "x" * (limit + 1), str(limit)),
            (
                "one byte over, in two-byte characters",
                "\u00e9" * (limit // 2 + 1),
                str(limit),
            ),
            ("a NUL byte", "before\x00after\n", "NUL"),
        ):
            with self.subTest(body=name):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        self.session_dir,
                        body,
                        30.0,
                        self._builder("", "--"),
                    )
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertIn(expected, str(caught.exception))
                self.assertEqual(self._messages(), [])

    def test_a_courier_only_peer_is_refused_a_project_before_anything_starts(
        self,
    ):
        """All three courier targets fail before connector import or publication."""
        for peer_id in ("hermes", "minimax", "qwen"):
            with self.subTest(peer=peer_id):
                project_session = os.path.join(
                    self.temp, "project-session-{0}".format(peer_id)
                )
                record.record(
                    project_session,
                    "session-create",
                    "This target cannot receive a project.\n",
                    initiator="sample-app",
                    peer=peer_id,
                    project=self.temp,
                )
                captured = io.StringIO()
                with mock.patch.object(
                    runner.connectors,
                    "resolve",
                    side_effect=AssertionError(
                        "courier project refusal reached connector resolution"
                    ),
                ):
                    with mock.patch("sys.stderr", captured), mock.patch(
                        "sys.stdin", io.StringIO("A question.\n")
                    ):
                        status = cli.main(
                            [
                                "run",
                                "--session",
                                project_session,
                            ]
                        )
                self.assertEqual(status, 1)
                self.assertIn(
                    "project for {0}".format(peer_id), captured.getvalue()
                )
                self.assertIn("Next action:", captured.getvalue())
                self.assertEqual(
                    sorted(os.listdir(session.messages_dir(project_session))), []
                )

    def test_command_surface_rejects_removed_run_and_record_arguments(self):
        removed = (
            ["run", "--session", self.session_dir, "--peer", "hermes"],
            ["run", "--session", self.session_dir, "--project", self.temp],
            ["run", "--session", self.session_dir, "--review-base", "a"],
            [
                "record",
                "--session",
                self.session_dir,
                "--kind",
                "note",
                "--local",
                "old",
            ],
            [
                "record",
                "--session",
                self.session_dir,
                "--kind",
                "note",
                "--workflow",
                "old",
            ],
        )
        for argv in removed:
            with self.subTest(argv=argv):
                captured = io.StringIO()
                with mock.patch("sys.stderr", captured):
                    with mock.patch("sys.stdin", io.StringIO("Body.\n")):
                        self.assertEqual(cli.main(argv), 1)
                self.assertIn("unrecognized arguments", captured.getvalue())
        self.assertEqual(self._messages(), [])


if __name__ == "__main__":
    unittest.main()

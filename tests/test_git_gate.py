"""The Git finish line, and the ways it must refuse to open.

An external review is only worth something if it describes the exact code that
was looked at. These checks take that claim apart from the outside: they hand
the runner a review that is wrong in one specific way and confirm that the gate
stays shut, that nothing was published, and that no evidence file was left
behind.

Two controls make the refusals mean something. A proper review of a clean,
correctly bound head does open the gate, and does publish a response carrying
the three binding lines the runner supplies. And `REJECT` and `ASK_USER` are
published like any other answer while opening nothing - they are decisions a
reviewer really made, not failures.

Every review here also proves the handover a reviewer depends on: the builder
that stands in for a connector records the evidence path the runner gave it, and
the checks confirm that path was a real file holding the `baseline..head` diff
at the moment the command was composed, and is gone once the turn returned.

Everything runs against a throwaway repository built for the purpose and deleted
afterwards, and against the fake peer. No real harness is involved anywhere.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from typing import NamedTuple, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bridge import gitgate, record, runner, session  # noqa: E402
from bridge.connectors import PeerCommand  # noqa: E402
from bridge.errors import BridgeError, Failure  # noqa: E402
from bridge.verdict import ACCEPT, ASK_USER, REJECT  # noqa: E402
from tests import synthetic_repo  # noqa: E402

FAKE_PEER = os.path.join(REPO_ROOT, "tests", "fake_peer.py")

REVIEW_BODY = "Review the cumulative change against the approved plan.\n"


class HandedEvidence(NamedTuple):
    """What the runner gave the builder, recorded as the builder saw it."""

    path: Optional[str]
    existed: bool
    text: Optional[str]


class GitFinishLine(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-check-")
        self.repositories = []
        self.commit_error = []
        self.handed = []
        self.timer = None
        self.repo = self.new_repository()
        self.session_dir = os.path.join(self.temp, "session")
        record.record(
            self.session_dir,
            "session-create",
            "Prove the Git gate cannot be opened by a review that is wrong.\n",
            local="codex",
            peer="claude",
            workflow="external-review",
            project=self.repo.project,
        )
        self.evidence_before = self._evidence_files()

    def tearDown(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer.join()
        self.assertEqual(
            self._evidence_files(),
            self.evidence_before,
            "a review-evidence file outlived the turn that made it",
        )
        for repository in self.repositories:
            repository.cleanup()
        shutil.rmtree(self.temp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def _evidence_files(self):
        pattern = os.path.join(
            tempfile.gettempdir(), gitgate.REVIEW_EVIDENCE_PREFIX + "*"
        )
        return set(glob.glob(pattern))

    def new_repository(self):
        repository = synthetic_repo.create()
        self.repositories.append(repository)
        return repository

    def seal(self, baseline):
        record.record(
            self.session_dir,
            "implementation-start",
            "Implementation begins here.\n",
            project=self.repo.project,
            baseline=baseline,
        )

    def ready(self):
        """Seal the initial commit and put one commit of work on top of it."""
        self.seal(self.repo.initial_commit)
        return self.repo.add_commit("Implementation work")

    def builder(self, mode, *extra):
        """What the runner calls to compose the command for one turn.

        A real connector uses the evidence path it is given to name that exact
        file among the paths it lets the peer read. Standing in for one, this
        records what it was handed - the path, whether it was a real file at
        that moment, and what it held - so a check can prove a reviewing peer
        could actually be told where the diff is.
        """
        argv = (sys.executable, FAKE_PEER, mode) + tuple(extra)

        def build(evidence_path):
            existed = bool(evidence_path) and os.path.isfile(evidence_path)
            self.handed.append(
                HandedEvidence(
                    path=evidence_path,
                    existed=existed,
                    text=self.read(evidence_path) if existed else None,
                )
            )
            return PeerCommand(
                argv=argv,
                cwd=self.temp,
                env=tuple(os.environ.items()),
            )

        return build

    def expected_diff(self, base, head):
        """The cumulative diff, produced without going through the bridge."""
        completed = subprocess.run(
            [
                "git",
                "-C",
                self.repo.project,
                "--no-pager",
                "-c",
                "diff.external=",
                "-c",
                "core.attributesFile=" + os.devnull,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "{0}..{1}".format(base, head),
            ],
            cwd=self.repo.project,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr.decode("utf-8", "replace"),
        )
        return completed.stdout.decode("utf-8", "replace")

    def assert_evidence_reached_the_builder(self, base, head, calls=1):
        """The builder was told where the diff was, and it is gone now."""
        diff = self.expected_diff(base, head)
        self.assertIn("diff --git ", diff, "there was no change to review")
        self.assertEqual(len(self.handed), calls)
        for handed in self.handed:
            self.assertIsNotNone(
                handed.path, "the builder was handed no evidence path"
            )
            self.assertTrue(
                handed.existed,
                "{0} was not a real file when the command was "
                "composed".format(handed.path),
            )
            self.assertEqual(
                handed.text,
                diff,
                "the evidence was not the baseline..head diff",
            )
            self.assertFalse(
                os.path.exists(handed.path),
                "the evidence file outlived the turn",
            )

    def review(self, mode, base, head, *extra, **kwargs):
        project = kwargs.pop("project", None) or self.repo.project
        timeout = kwargs.pop("timeout", 30.0)
        return runner.run_turn(
            self.session_dir,
            "claude",
            REVIEW_BODY,
            self.builder(mode, *extra),
            timeout,
            project=project,
            review_base=base,
            review_head=head,
        )

    def refuse(self, failure, mode, base, head, *extra, **kwargs):
        with self.assertRaises(BridgeError) as caught:
            self.review(mode, base, head, *extra, **kwargs)
        self.assertEqual(caught.exception.failure, failure)
        self.assertEqual(self.responses(), [])
        return caught.exception

    def messages(self):
        return sorted(os.listdir(session.messages_dir(self.session_dir)))

    def responses(self):
        return [
            name
            for name in self.messages()
            if name.endswith(session.PEER_TO_LOCAL_SUFFIX)
        ]

    def requests(self):
        return [
            name
            for name in self.messages()
            if name.endswith(session.LOCAL_TO_PEER_SUFFIX)
        ]

    def read(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()

    # -- controls ----------------------------------------------------------

    def test_a_bound_accept_opens_the_finish_line(self):
        head = self.ready()
        result = self.review("accept", self.repo.initial_commit, head)

        self.assertEqual(result.verdict, ACCEPT)
        self.assertTrue(result.git_unlocked)

        response = self.read(result.response_path)
        self.assertIn("From: claude\nTo: codex\n", response)
        self.assertIn(
            "Review-Request: {0}\n".format(
                session.format_sequence(result.request_sequence)
            ),
            response,
        )
        self.assertIn(
            "Review-Base: {0}\n".format(self.repo.initial_commit), response
        )
        self.assertIn("Review-Head: {0}\n".format(head), response)
        self.assertTrue(
            response.rstrip().endswith("Agent-Bridge-Verdict: ACCEPT")
        )
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )

    def test_reject_is_published_and_opens_nothing(self):
        head = self.ready()
        result = self.review("reject", self.repo.initial_commit, head)
        self.assertEqual(result.verdict, REJECT)
        self.assertFalse(result.git_unlocked)
        self.assertEqual(len(self.responses()), 1)
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )

    def test_ask_user_is_published_and_opens_nothing(self):
        head = self.ready()
        result = self.review("ask-user", self.repo.initial_commit, head)
        self.assertEqual(result.verdict, ASK_USER)
        self.assertFalse(result.git_unlocked)
        self.assertEqual(len(self.responses()), 1)
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )

    # -- an answer that is not a verdict -----------------------------------

    def test_an_answer_that_is_not_a_verdict_opens_nothing(self):
        head = self.ready()
        cases = (
            ("unknown-verdict", Failure.INVALID_VERDICT),
            ("trailing-space", Failure.INVALID_VERDICT),
            ("lowercase", Failure.INVALID_VERDICT),
            ("fenced", Failure.INVALID_VERDICT),
            ("marker-early", Failure.INVALID_VERDICT),
            ("plain", Failure.INVALID_VERDICT),
            ("empty", Failure.EMPTY_RESPONSE),
        )
        for mode, failure in cases:
            with self.subTest(mode=mode):
                self.refuse(failure, mode, self.repo.initial_commit, head)
        self.assertEqual(len(self.requests()), len(cases))
        self.assertEqual(self.responses(), [])
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head, calls=len(cases)
        )

    # -- the wrong commits --------------------------------------------------

    def test_a_baseline_that_was_not_sealed_is_refused(self):
        self.ready()
        other_base = self.repo.add_commit("Another commit")
        newer = self.repo.add_commit("And another")
        self.refuse(Failure.BASELINE_CHANGED, "accept", other_base, newer)
        self.assertEqual(self.requests(), [])

    def test_a_baseline_on_a_divergent_branch_is_refused(self):
        branch = self.repo._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        self.repo._git(
            "checkout", "--quiet", "-b", "side", self.repo.initial_commit
        )
        divergent = self.repo.add_commit("Side work", filename="side.txt")
        self.repo._git("checkout", "--quiet", branch)
        head = self.repo.add_commit("Main work")

        self.seal(divergent)
        self.refuse(Failure.BASELINE_NOT_ANCESTOR, "accept", divergent, head)
        self.assertEqual(self.requests(), [])

    def test_a_baseline_equal_to_the_head_is_refused(self):
        self.seal(self.repo.initial_commit)
        self.refuse(
            Failure.BASELINE_NOT_ANCESTOR,
            "accept",
            self.repo.initial_commit,
            self.repo.initial_commit,
        )
        self.assertEqual(self.requests(), [])

    # -- the wrong repository, or a changed one -----------------------------

    def test_a_different_repository_is_refused(self):
        head = self.ready()
        elsewhere = self.new_repository()
        self.refuse(
            Failure.REPOSITORY_CHANGED,
            "accept",
            self.repo.initial_commit,
            head,
            project=elsewhere.project,
        )
        self.assertEqual(self.requests(), [])

    def test_an_unclean_worktree_is_refused(self):
        head = self.ready()
        self.repo.make_dirty()
        self.refuse(
            Failure.DIRTY_WORKTREE, "accept", self.repo.initial_commit, head
        )
        self.assertEqual(self.requests(), [])

    # -- the head moved -----------------------------------------------------

    def test_a_head_that_is_no_longer_current_is_refused_before_the_call(self):
        first = self.ready()
        self.repo.add_commit("Work that came afterwards")
        self.refuse(
            Failure.HEAD_CHANGED, "accept", self.repo.initial_commit, first
        )
        self.assertEqual(
            self.requests(),
            [],
            "the peer was asked about a head that was already gone",
        )

    def test_a_commit_during_the_review_voids_the_verdict(self):
        head = self.ready()

        def commit_now():
            try:
                self.repo.add_commit("Committed while the review was running")
            except Exception as exc:  # surfaced in the assertion below
                self.commit_error.append(exc)

        self.timer = threading.Timer(0.5, commit_now)
        self.timer.start()

        with self.assertRaises(BridgeError) as caught:
            self.review(
                "slow-accept",
                self.repo.initial_commit,
                head,
                "2.0",
                timeout=60.0,
            )
        self.timer.join()
        self.assertEqual(self.commit_error, [])
        self.assertEqual(caught.exception.failure, Failure.HEAD_CHANGED)
        self.assertNotEqual(self.repo.head(), head)
        self.assertEqual(
            self.responses(),
            [],
            "an accepted review of a head that moved was published anyway",
        )
        self.assertEqual(len(self.requests()), 1)
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )


if __name__ == "__main__":
    unittest.main()

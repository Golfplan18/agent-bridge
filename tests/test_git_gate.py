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
at the moment the command was composed, and is gone once the turn returned. One
check goes further and proves the peer really opened that file, by hiding a
token in the reviewed change that can reach the peer no other way.

Everything runs against a throwaway repository built for the purpose and deleted
afterwards, and against the fake peer. No real harness is involved anywhere. Two
conditions cannot be produced on demand any other way - a temporary filesystem
that will not take a file, and a step that outruns its deadline - and those are
forced for the length of one call.

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
import time
import unittest
import uuid
from typing import NamedTuple, Optional
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bridge import gitgate, record, runner, session  # noqa: E402
from bridge.connectors import PeerCommand  # noqa: E402
from bridge.errors import BridgeError, Failure  # noqa: E402
from bridge.verdict import ACCEPT, ASK_USER, REJECT, read_verdict  # noqa: E402
from tests import synthetic_repo  # noqa: E402

FAKE_PEER = os.path.join(REPO_ROOT, "tests", "fake_peer.py")

REVIEW_BODY = "Review the cumulative change against the approved plan.\n"

#: The shape of token the fake peer looks for inside the evidence file. A fresh
#: one is committed for the check that proves the peer really read the diff.
EVIDENCE_TOKEN_PREFIX = "AGENT-BRIDGE-EVIDENCE-TOKEN-"


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

    def builder(self, mode, *extra, **kwargs):
        """What the runner calls to compose the command for one turn.

        A real connector uses the evidence path it is given to name that exact
        file in the restrictions it applies, and then declares both that path
        and the project root it granted. Standing in for one, this records
        what it was handed - the path, whether it was a real file at that
        moment, and what it held - so a check can prove a reviewing peer could
        actually be told where the diff is, and it declares exactly what it was
        given so the runner's own comparison passes.

        With `pass_evidence`, the path is also put on the fake peer's command
        line, which is how a peer that is meant to open the file is told where
        it is.
        """
        project = kwargs.pop("project", None) or self.repo.project
        pass_evidence = kwargs.pop("pass_evidence", False)

        def build(evidence_path, deadline):
            existed = bool(evidence_path) and os.path.isfile(evidence_path)
            self.handed.append(
                HandedEvidence(
                    path=evidence_path,
                    existed=existed,
                    text=self.read(evidence_path) if existed else None,
                )
            )
            argv = (sys.executable, FAKE_PEER, mode) + tuple(extra)
            if pass_evidence:
                argv = argv + (evidence_path or "",)
            return PeerCommand(
                argv=argv,
                cwd=self.temp,
                env=tuple(os.environ.items()),
                project_root=project,
                review_evidence=evidence_path,
            )

        return build

    def misdeclaring_builder(self, named):
        """A connector that grants the peer some other file, or names none.

        Nothing else about it is wrong: the argument vector would have run a
        peer that answers properly. What the runner has to notice is only that
        what was granted is not what was written.
        """

        def build(evidence_path, deadline):
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "accept"),
                cwd=self.temp,
                env=tuple(os.environ.items()),
                project_root=self.repo.project,
                review_evidence=named,
            )

        return build

    def request_text(self):
        """The most recently published request, read back off the disk."""
        return self.read(
            os.path.join(
                session.messages_dir(self.session_dir), self.requests()[-1]
            )
        )

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
            self.builder(mode, *extra, project=project, **kwargs),
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

    def refuse_but_keep(self, mode, base, head, *extra, **kwargs):
        """An invalid verdict fails, and the peer's prose survives anyway.

        The opposite expectation to `refuse`: this peer exited cleanly and wrote
        real text, so one more response message exists afterwards. What it must
        not have is any binding - which the checks below prove separately.
        """
        before = len(self.responses())
        with self.assertRaises(BridgeError) as caught:
            self.review(mode, base, head, *extra, **kwargs)
        self.assertEqual(caught.exception.failure, Failure.INVALID_VERDICT)
        self.assertEqual(
            len(self.responses()),
            before + 1,
            "the peer's text was thrown away instead of kept",
        )
        return caught.exception

    def latest_response_text(self):
        return self.read(
            os.path.join(
                session.messages_dir(self.session_dir), self.responses()[-1]
            )
        )

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
        modes = (
            "unknown-verdict",
            "trailing-space",
            "lowercase",
            "fenced",
            "marker-early",
            "plain",
        )
        head = self.ready()
        for mode in modes:
            with self.subTest(mode=mode):
                self.refuse_but_keep(mode, self.repo.initial_commit, head)
        self.assertEqual(len(self.requests()), len(modes))
        self.assertEqual(len(self.responses()), len(modes))
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head, calls=len(modes)
        )

    def test_an_unfinished_call_keeps_nothing(self):
        """Text from a call that did not finish cleanly may be a fragment."""
        head = self.ready()
        self.refuse(
            Failure.EMPTY_RESPONSE, "empty", self.repo.initial_commit, head
        )
        self.refuse(
            Failure.PEER_FAILURE, "fail", self.repo.initial_commit, head
        )
        self.assertEqual(len(self.requests()), 2)
        self.assertEqual(self.responses(), [])

    def test_a_kept_invalid_verdict_carries_no_review_authority(self):
        """The prose survives; every field that could bind it does not."""
        head = self.ready()
        error = self.refuse_but_keep(
            "unknown-verdict", self.repo.initial_commit, head
        )

        kept = self.latest_response_text()
        header = kept.split("\n\n", 1)[0]
        for field in ("Review-Request:", "Review-Base:", "Review-Head:"):
            self.assertNotIn(
                field,
                header,
                "a kept invalid verdict must bind to nothing",
            )
        for commit in (self.repo.initial_commit, head):
            self.assertNotIn(
                commit, header, "a kept invalid verdict named a commit"
            )

        # The peer's own words survived, including the line it got wrong.
        self.assertIn(REVIEW_BODY.strip(), kept)
        self.assertIn("Agent-Bridge-Verdict: MAYBE", kept)

        # And the failure says where the text went.
        self.assertIn(self.responses()[-1], error.detail or "")

        # A kept response is not a decision, so nothing may read one out of it.
        self.assertEqual(read_verdict(kept).failure, Failure.INVALID_VERDICT)

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

    # -- the evidence a reviewer actually read ------------------------------

    def test_the_peer_is_proved_to_have_read_the_evidence(self):
        """A token that can reach the peer only through the diff comes back.

        The reviewed change carries a fresh random token. It is nowhere in the
        outgoing body, so a peer that merely pretended to look cannot produce
        it. Quoting it back is the proof.
        """
        self.seal(self.repo.initial_commit)
        token = EVIDENCE_TOKEN_PREFIX + uuid.uuid4().hex
        head = self.repo.add_commit(
            "Work carrying a token nothing else can supply",
            filename="token.txt",
            text=token + "\n",
        )

        result = self.review(
            "read-evidence",
            self.repo.initial_commit,
            head,
            pass_evidence=True,
        )
        self.assertEqual(result.verdict, ACCEPT)
        self.assertTrue(result.git_unlocked)
        self.assertIn(
            token,
            self.read(result.response_path),
            "the peer did not quote what was inside the evidence",
        )

        request = self.request_text()
        self.assertNotIn(
            token, request, "the token was sent instead of being read"
        )
        self.assertEqual(
            request.split("\n\n", 1)[0],
            "# Message {0}\nFrom: codex\nTo: claude\n"
            "Review-Evidence: {1}".format(
                session.format_sequence(result.request_sequence),
                self.handed[0].path,
            ),
        )
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )

    def test_evidence_changed_while_the_peer_had_it_voids_the_turn(self):
        """An answer about a file somebody rewrote is about nothing knowable."""
        head = self.ready()
        self.refuse(
            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
            "rewrite-evidence",
            self.repo.initial_commit,
            head,
            pass_evidence=True,
        )
        self.assertEqual(len(self.requests()), 1)

    def test_a_connector_that_grants_the_wrong_file_never_starts_a_peer(self):
        """What was granted has to be what was written, or nothing is sent."""
        head = self.ready()
        elsewhere = os.path.join(self.temp, "not-the-evidence.diff")
        for named in (elsewhere, None):
            with self.subTest(named=named):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        self.session_dir,
                        "claude",
                        REVIEW_BODY,
                        self.misdeclaring_builder(named),
                        30.0,
                        project=self.repo.project,
                        review_base=self.repo.initial_commit,
                        review_head=head,
                    )
                self.assertEqual(
                    caught.exception.failure,
                    Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
                )
                self.assertEqual(
                    self.requests(),
                    [],
                    "a request was published, so the peer was started",
                )
                self.assertEqual(self.responses(), [])

    def test_evidence_that_cannot_be_written_is_a_named_failure(self):
        """A full or unwritable temporary area is a defined, actionable stop."""
        head = self.ready()
        with mock.patch.object(
            gitgate.tempfile, "mkstemp", side_effect=OSError("no space left")
        ):
            error = self.refuse(
                Failure.REVIEW_EVIDENCE_UNAVAILABLE,
                "accept",
                self.repo.initial_commit,
                head,
            )
        self.assertIn("Next action:", str(error))
        self.assertIn("TMPDIR", str(error))
        self.assertEqual(self.requests(), [])
        self.assertEqual(self._evidence_files(), self.evidence_before)

    def test_evidence_that_outruns_the_deadline_publishes_nothing(self):
        """One deadline covers writing the difference, not just the peer call."""
        head = self.ready()
        real_git = gitgate._git

        def slow_diff(project, args, deadline, *rest, **kwargs):
            if "diff" in args:
                time.sleep(max(0.0, deadline.remaining()) + 0.05)
            return real_git(project, args, deadline, *rest, **kwargs)

        with mock.patch.object(gitgate, "_git", slow_diff):
            self.refuse(
                Failure.TIMEOUT,
                "accept",
                self.repo.initial_commit,
                head,
                timeout=2.0,
            )
        self.assertEqual(self.requests(), [])
        self.assertEqual(self._evidence_files(), self.evidence_before)

    # -- what the repository is allowed to say about itself -----------------

    def test_a_replaced_commit_cannot_change_what_the_reviewer_reads(self):
        """Git may be told one commit stands for another. A review may not.

        The repository is given a mapping from the reviewed head to a commit
        with different contents on the same parent. Ordinary Git honours it,
        which this check confirms first; the review must not, or the reviewer
        would be judging code that `Review-Head` does not name.
        """
        true_line = "The contents that were really committed."
        decoy_line = "AGENT-BRIDGE-REPLACED-CONTENTS must never be reviewed."
        self.seal(self.repo.initial_commit)
        head = self.repo.add_commit(
            "The work that is really under review",
            filename="work.txt",
            text=true_line + "\n",
        )
        branch = self.repo._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        self.repo._git(
            "checkout", "--quiet", "-b", "decoy", self.repo.initial_commit
        )
        decoy = self.repo.add_commit(
            "Contents somebody would rather have reviewed",
            filename="work.txt",
            text=decoy_line + "\n",
        )
        self.repo._git("checkout", "--quiet", branch)
        self.repo._git("replace", head, decoy)

        misled = self.repo._git(
            "diff", "{0}..{1}".format(self.repo.initial_commit, head)
        )
        self.assertIn(
            decoy_line,
            misled,
            "the replacement was not in effect, so this check proves nothing",
        )

        result = self.review("accept", self.repo.initial_commit, head)
        self.assertTrue(result.git_unlocked)
        self.assertEqual(len(self.handed), 1)
        evidence = self.handed[0].text
        self.assertIn(
            true_line, evidence, "the reviewer was not shown the real head"
        )
        self.assertNotIn(
            "AGENT-BRIDGE-REPLACED-CONTENTS",
            evidence,
            "a replacement object decided what the reviewer read",
        )
        published = self.read(result.response_path)
        self.assertIn("Review-Head: {0}\n".format(head), published)

    def test_an_ignored_file_is_uncommitted_too_and_is_never_deleted(self):
        """Ignoring a file says nothing about whether a peer can read it."""
        self.seal(self.repo.initial_commit)
        head = self.repo.add_commit(
            "Ignore the scratch file",
            filename=".gitignore",
            text="scratch.txt\n",
        )
        ignored = os.path.join(self.repo.project, "scratch.txt")
        with open(ignored, "w", encoding="utf-8") as stream:
            stream.write("Something no commit contains.\n")

        error = self.refuse(
            Failure.DIRTY_WORKTREE, "accept", self.repo.initial_commit, head
        )
        self.assertIn("scratch.txt", str(error))
        self.assertIn("ignored", str(error))
        self.assertEqual(self.requests(), [])
        self.assertTrue(
            os.path.exists(ignored),
            "an ignored file was deleted instead of being reported",
        )

    # -- the repository outranks a fumbled last line ------------------------

    def test_a_commit_during_the_review_outranks_a_malformed_verdict(self):
        """Keeping prose matters only if the code it describes is still there."""
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
                "slow-unknown-verdict",
                self.repo.initial_commit,
                head,
                "2.0",
                timeout=60.0,
            )
        self.timer.join()
        self.assertEqual(self.commit_error, [])
        self.assertEqual(caught.exception.failure, Failure.HEAD_CHANGED)
        self.assertEqual(
            self.responses(),
            [],
            "a malformed answer about a head that moved was kept anyway",
        )
        self.assertEqual(len(self.requests()), 1)


if __name__ == "__main__":
    unittest.main()

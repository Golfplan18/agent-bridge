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
followed by this turn's own token at the moment the command was composed, and is
gone once the turn returned.

Reading that evidence is not optional here, because it is not optional in the
product. Every peer in this file is given the evidence path unless a check is
specifically about a peer that was not, and the check that is - a perfectly
formed `ACCEPT` from a peer that never opened the file - proves the finish line
stays shut. One further check hides a canary inside the reviewed change itself,
which proves the difference reached the peer rather than only the line the
runner appended to it.

Several checks are about what a repository is allowed to say about itself. Three
of them are about hiding: an untracked file, a whole tracked submodule, and a
tracked file the index says not to look at. One is about the filesystem-monitor
helper a repository names, which the gate must not run. And one is about a
partial clone, which must not be allowed to reach its remote for a missing
object while the gate reads it.

The filesystem-monitor check proves that one setting and is named for it. It is
not a claim that nothing a repository says can make the gate run a program: a
configured `clean` or `process` content filter still runs while `git status`
works out whether the worktree is clean. The repository selects the filter
through its committed attributes, but the command itself has to be in the user's
own effective Git configuration, which does not travel with a clone. Smudge
filters are not part of that - the gate never checks anything out - and
text-conversion filters are switched off. It is an accepted residual of the
cooperative same-user trust boundary, stated in `gitgate` and INTERFACE.md:
use Agent Bridge only with repositories and Git configuration you trust. Nothing
here isolates a hostile repository.

Everything runs against a throwaway repository built for the purpose and deleted
afterwards, and against the fake peer. No real harness is involved anywhere, and
nothing reaches the network: the one check that needs a remote makes a bare
clone of the throwaway repository in a directory it owns and deletes. Four
conditions cannot be produced on demand any other way - a temporary filesystem
that will not take a file, one that will not then remove it, a step that outruns
its deadline, and a stop signal arriving at one exact instant - and those are
forced for the length of one call.

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
import threading
import time
import unittest
import uuid
from typing import NamedTuple, Optional
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bridge import gitgate, peer, record, runner, session  # noqa: E402
from bridge.connectors import PeerCommand  # noqa: E402
from bridge.errors import BridgeError, Failure  # noqa: E402
from bridge.verdict import ACCEPT, ASK_USER, REJECT, read_verdict  # noqa: E402
from tests import synthetic_repo  # noqa: E402

FAKE_PEER = os.path.join(REPO_ROOT, "tests", "fake_peer.py")

REVIEW_BODY = "Review the cumulative change against the approved plan.\n"

#: The shape of canary the fake peer looks for inside the reviewed change. A
#: fresh one is committed for the check that proves the peer really read the
#: difference, and not merely the token line the runner appends to it.
DIFF_CANARY_PREFIX = "AGENT-BRIDGE-DIFF-CANARY-"

#: What the runner appends to every evidence file it writes. A response that
#: does not quote the value cannot become an acceptance.
TOKEN_LINE = re.compile(
    "^" + re.escape(gitgate.EVIDENCE_TOKEN_PREFIX) + "([0-9a-f]{32})$",
    re.MULTILINE,
)

#: A filesystem-monitor helper is an ordinary program of the repository's
#: choosing. This one leaves a mark when it runs and answers Git correctly
#: enough not to disturb the command that started it.
FSMONITOR_HELPER = """#!/bin/sh
: > "{0}"
printf '/\000'
exit 0
"""


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
        and the project root it granted. Standing in for one, this records what
        it was handed - the path, whether it was a real file at that moment, and
        what it held - so a check can prove a reviewing peer could actually be
        told where the diff is, and it declares exactly what it was given so the
        runner's own comparison passes.

        The path is also put on the fake peer's command line, which is how a
        peer is told where to read. That is the default because a peer that does
        not read the evidence cannot produce an accepted review, so every check
        that expects one has to go through a peer that did. Passing
        `read_evidence=False` makes a peer that was never told, which is what
        the checks about a missing token and about an empty answer need.
        """
        project = kwargs.pop("project", None) or self.repo.project
        read_evidence = kwargs.pop("read_evidence", True)

        def build(evidence_path, deadline):
            existed = bool(evidence_path) and os.path.isfile(evidence_path)
            self.handed.append(
                HandedEvidence(
                    path=evidence_path,
                    existed=existed,
                    text=self.read(evidence_path) if existed else None,
                )
            )
            argv = (sys.executable, FAKE_PEER)
            if read_evidence and evidence_path:
                argv = argv + ("--evidence", evidence_path)
            argv = argv + (mode,) + tuple(extra)
            return PeerCommand(
                argv=argv,
                cwd=self.temp,
                env=tuple(os.environ.items()),
                project_root=project,
                review_evidence=evidence_path,
            )

        return build

    def precheck_builder(self, seconds):
        """A connector whose own precheck runs longer than the turn allows.

        A real connector asks the harness its version, whether it is signed in,
        and whether the restriction switches are there. Those are programs, and
        they run inside the turn's deadline through the same bounded runner
        everything else uses. This one stands in for a precheck that hangs.
        """

        def build(evidence_path, deadline):
            existed = bool(evidence_path) and os.path.isfile(evidence_path)
            self.handed.append(
                HandedEvidence(path=evidence_path, existed=existed, text=None)
            )
            peer.run_bounded(
                argv=(sys.executable, FAKE_PEER, "hang", str(seconds)),
                cwd=self.temp,
                env=tuple(os.environ.items()),
                stdin_text="",
                deadline=deadline,
            )
            raise AssertionError("the precheck outlasted the deadline")

        return build

    def late_builder(self):
        """A connector that finishes, but only after the deadline has passed."""

        def build(evidence_path, deadline):
            existed = bool(evidence_path) and os.path.isfile(evidence_path)
            self.handed.append(
                HandedEvidence(path=evidence_path, existed=existed, text=None)
            )
            time.sleep(max(0.0, deadline.remaining()) + 0.05)
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "accept"),
                cwd=self.temp,
                env=tuple(os.environ.items()),
                project_root=self.repo.project,
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

    def plain_git(self, *args, **kwargs):
        """Ordinary Git, run the way Git ordinarily runs, for a control.

        None of the gate's overrides are applied - in particular not
        `GIT_NO_LAZY_FETCH`, which is explicitly removed in case the machine
        running these checks has it set. That is what makes a control worth
        having: if the same command behaves differently here and through the
        gate, the difference is the override doing something.

        Personal Git configuration is still pointed at an empty device, exactly
        as the synthetic-repository fixture does, so that a setting on this
        machine cannot decide what a check observes.
        """
        env = synthetic_repo._git_env()
        env.pop("GIT_NO_LAZY_FETCH", None)
        env.update(kwargs.pop("env", None) or {})
        cwd = kwargs.pop("cwd", None) or self.repo.project
        return subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
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
        """The builder was told where the diff was, and it is gone now.

        The file it was told about is the `baseline..head` difference followed
        by one token line the runner wrote for that turn alone. Both halves are
        checked, and the tokens of separate turns are checked to be different -
        a token that repeated would be a value a peer could have kept from an
        earlier call rather than read from this one.
        """
        diff = self.expected_diff(base, head)
        self.assertIn("diff --git ", diff, "there was no change to review")
        self.assertEqual(len(self.handed), calls)
        tokens = []
        for handed in self.handed:
            self.assertIsNotNone(
                handed.path, "the builder was handed no evidence path"
            )
            self.assertTrue(
                handed.existed,
                "{0} was not a real file when the command was "
                "composed".format(handed.path),
            )
            self.assertTrue(
                handed.text.startswith(diff),
                "the evidence did not begin with the baseline..head diff",
            )
            appended = handed.text[len(diff):]
            found = TOKEN_LINE.search(appended)
            self.assertIsNotNone(
                found, "the evidence carried no token line: {0!r}".format(appended)
            )
            self.assertEqual(
                appended,
                "\n" + found.group(0) + "\n",
                "the evidence held more than the diff and its token",
            )
            tokens.append(found.group(1))
            self.assertFalse(
                os.path.exists(handed.path),
                "the evidence file outlived the turn",
            )
        self.assertEqual(
            len(set(tokens)), len(tokens), "two turns used the same token"
        )
        return tokens

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

    def refuse_but_keep(self, failure, mode, base, head, *extra, **kwargs):
        """The call fails, and the peer's prose survives anyway.

        The opposite expectation to `refuse`, and it holds for the two failures
        where a peer exited cleanly and wrote real text: its last line was not a
        verdict, or its answer never quoted the evidence token. One more
        response message exists afterwards. What it must not have is any binding
        - which the checks below prove separately.
        """
        before = len(self.responses())
        with self.assertRaises(BridgeError) as caught:
            self.review(mode, base, head, *extra, **kwargs)
        self.assertEqual(caught.exception.failure, failure)
        self.assertEqual(
            len(self.responses()),
            before + 1,
            "the peer's text was thrown away instead of kept",
        )
        return caught.exception

    def assert_binds_to_nothing(self, kept, base, head):
        """A kept response carries no field and no commit that could bind it."""
        header = kept.split("\n\n", 1)[0]
        for field in ("Review-Request:", "Review-Base:", "Review-Head:"):
            self.assertNotIn(
                field, header, "a kept response must bind to nothing"
            )
        for commit in (base, head):
            self.assertNotIn(commit, header, "a kept response named a commit")

    def latest_response_text(self):
        return self.read(
            os.path.join(
                session.messages_dir(self.session_dir), self.responses()[-1]
            )
        )

    # -- controls ----------------------------------------------------------

    def test_a_bound_accept_opens_the_finish_line(self):
        """The one case that opens it, and the peer had to read to get there."""
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
        token = self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )[0]

        # The token is in the answer because the peer read it out of the file,
        # and it is nowhere in what was sent, so it cannot have come from there.
        self.assertIn(token, response)
        request = self.request_text()
        self.assertNotIn(token, request)
        self.assertIn(gitgate.EVIDENCE_TOKEN_PREFIX.strip(), request)

    def test_an_accept_from_a_peer_that_never_read_the_evidence_opens_nothing(
        self,
    ):
        """The gate condition: no token back, no acceptance, however it ends.

        This peer does everything else correctly. It exits cleanly, writes real
        prose, and ends with the exact `ACCEPT` line. What it never had is the
        evidence path, so it cannot quote the token, and without that there is
        nothing showing it ever saw the change it is passing judgment on.

        Its prose is kept, because prose may be worth reading. It is kept as an
        ordinary message binding to nothing, the call fails, and Git stays shut.
        """
        head = self.ready()
        error = self.refuse_but_keep(
            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
            "accept",
            self.repo.initial_commit,
            head,
            read_evidence=False,
        )

        kept = self.latest_response_text()
        self.assert_binds_to_nothing(kept, self.repo.initial_commit, head)
        self.assertTrue(kept.rstrip().endswith("Agent-Bridge-Verdict: ACCEPT"))
        self.assertIn(self.responses()[-1], error.detail or "")
        self.assertIn("Next action:", str(error))

        token = self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )[0]
        self.assertNotIn(token, kept, "the peer quoted a token it never read")

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
                self.refuse_but_keep(
                    Failure.INVALID_VERDICT,
                    mode,
                    self.repo.initial_commit,
                    head,
                )
        self.assertEqual(len(self.requests()), len(modes))
        self.assertEqual(len(self.responses()), len(modes))
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head, calls=len(modes)
        )

    def test_an_unfinished_call_keeps_nothing(self):
        """Text from a call that did not finish cleanly may be a fragment.

        Neither peer here is given the evidence, because neither is about the
        evidence: one says nothing at all and the other exits with a failure,
        and both must be reported as what they are rather than as a missing
        token.
        """
        head = self.ready()
        self.refuse(
            Failure.EMPTY_RESPONSE,
            "empty",
            self.repo.initial_commit,
            head,
            read_evidence=False,
        )
        self.refuse(
            Failure.PEER_FAILURE,
            "fail",
            self.repo.initial_commit,
            head,
            read_evidence=False,
        )
        self.assertEqual(len(self.requests()), 2)
        self.assertEqual(self.responses(), [])

    def test_a_kept_invalid_verdict_carries_no_review_authority(self):
        """The prose survives; every field that could bind it does not."""
        head = self.ready()
        error = self.refuse_but_keep(
            Failure.INVALID_VERDICT,
            "unknown-verdict",
            self.repo.initial_commit,
            head,
        )

        kept = self.latest_response_text()
        self.assert_binds_to_nothing(kept, self.repo.initial_commit, head)

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

    def test_the_peer_is_proved_to_have_read_the_change_itself(self):
        """A canary that can reach the peer only through the diff comes back.

        The runner's own token proves the evidence file was opened. This proves
        something narrower and worth proving separately: that the file really
        carried the change under review. The reviewed commit contains a fresh
        random canary, it is nowhere in the outgoing body, and the peer quotes
        it back.
        """
        self.seal(self.repo.initial_commit)
        canary = DIFF_CANARY_PREFIX + uuid.uuid4().hex
        head = self.repo.add_commit(
            "Work carrying a canary nothing else can supply",
            filename="canary.txt",
            text=canary + "\n",
        )

        result = self.review(
            "read-evidence", self.repo.initial_commit, head
        )
        self.assertEqual(result.verdict, ACCEPT)
        self.assertTrue(result.git_unlocked)
        published = self.read(result.response_path)
        self.assertIn(
            canary,
            published,
            "the peer did not quote what was inside the evidence",
        )

        request = self.request_text()
        self.assertNotIn(
            canary, request, "the canary was sent instead of being read"
        )
        self.assertEqual(
            request.split("\n\n", 1)[0],
            "# Message {0}\nFrom: codex\nTo: claude\n"
            "Review-Evidence: {1}".format(
                session.format_sequence(result.request_sequence),
                self.handed[0].path,
            ),
        )
        token = self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )[0]
        self.assertIn(token, published)
        self.assertNotIn(token, request)

    def test_the_request_asks_for_the_token_without_naming_it(self):
        """The instruction is sent; the value it asks for never is."""
        head = self.ready()
        result = self.review("accept", self.repo.initial_commit, head)
        self.assertTrue(result.git_unlocked)

        request = self.request_text()
        self.assertIn(REVIEW_BODY.strip(), request)
        self.assertIn(
            gitgate.REVIEW_EVIDENCE_INSTRUCTION.strip(),
            request,
            "the peer was never told to copy the token back",
        )
        token = self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
        )[0]
        self.assertNotIn(
            token,
            request,
            "the value was sent, so quoting it would prove nothing",
        )

    def test_evidence_changed_while_the_peer_had_it_voids_the_turn(self):
        """An answer about a file somebody rewrote is about nothing knowable."""
        head = self.ready()
        self.refuse(
            Failure.REVIEW_EVIDENCE_NOT_DELIVERED,
            "rewrite-evidence",
            self.repo.initial_commit,
            head,
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

    def test_a_partly_written_evidence_file_that_will_not_go_is_reported(self):
        """A file left on the disk is a cleanup failure, not a shrug.

        Writing the evidence fails, and removing what was written fails too, so
        a partly written file survives. Reporting only that the evidence was
        unavailable would be true and useless: somebody has a file they were
        never told about. The failure has to be the one that says so.
        """
        head = self.ready()
        made = {}
        real_mkstemp = gitgate.tempfile.mkstemp
        real_fsync = os.fsync
        real_unlink = os.unlink

        def mkstemp(*args, **kwargs):
            handle, path = real_mkstemp(*args, **kwargs)
            if kwargs.get("prefix") == gitgate.REVIEW_EVIDENCE_PREFIX:
                made["handle"] = handle
                made["path"] = path
            return handle, path

        def fsync(handle):
            if handle == made.get("handle"):
                raise OSError("forced write failure")
            return real_fsync(handle)

        def unlink(path, *args, **kwargs):
            if path == made.get("path"):
                raise OSError("forced removal failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(gitgate.tempfile, "mkstemp", mkstemp):
            with mock.patch("os.fsync", fsync):
                with mock.patch("os.unlink", unlink):
                    error = self.refuse(
                        Failure.CLEANUP_FAILURE,
                        "accept",
                        self.repo.initial_commit,
                        head,
                    )

        left = made["path"]
        self.assertIn(left, str(error))
        self.assertIn("forced removal failure", str(error))
        self.assertIn("Next action:", str(error))
        self.assertEqual(self.requests(), [])
        self.assertTrue(
            os.path.exists(left),
            "nothing was left behind, so this check proves nothing",
        )
        # This check made the file the product could not remove, so this check
        # removes it - and the teardown then confirms none survived.
        real_unlink(left)

    def test_a_stop_while_the_evidence_is_being_deleted_still_deletes_it(self):
        """Being stopped must not be a way to leave the evidence behind.

        The evidence file has to be gone whatever happens, and the awkward
        moment is the removal itself. A termination arriving while `os.unlink`
        is running used to raise where it landed: the turn reported that it had
        been stopped, the lock was released, and the file stayed on the disk
        with nothing saying so.

        The signal is delivered from inside the removal rather than aimed at it
        from outside, which is the only way to be sure it lands there. Stops are
        now written down for the length of the removal and raised once it is
        done, so the turn still ends as a stop - and the file is gone. Before
        that change, this same check leaves the file behind and the teardown
        assertion catches it, which is what makes the check discriminate rather
        than merely pass.
        """
        head = self.ready()
        real_unlink = os.unlink
        removed = []

        def unlink_after_a_stop(path, *args, **kwargs):
            name = os.path.basename(str(path))
            if name.startswith(gitgate.REVIEW_EVIDENCE_PREFIX) and not removed:
                removed.append(path)
                os.kill(os.getpid(), signal.SIGTERM)
            return real_unlink(path, *args, **kwargs)

        with mock.patch("os.unlink", unlink_after_a_stop):
            with self.assertRaises(peer.SignalStop):
                self.review("accept", self.repo.initial_commit, head)

        self.assertEqual(
            len(removed), 1, "the evidence was never put up for removal"
        )
        self.assertFalse(
            os.path.exists(removed[0]),
            "a stop during the removal left the evidence on the disk",
        )
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(
            self.responses(),
            [],
            "a turn that was stopped published an answer anyway",
        )

    def test_a_stop_while_the_evidence_is_being_written_stays_a_stop(self):
        """Stopped is stopped, and is never reported as a missing file.

        A termination arriving mid-write used to be turned into
        `REVIEW_EVIDENCE_UNAVAILABLE`, which told a person to go and free up
        disk space over a key they had pressed themselves. The partly written
        file is still cleared away - that part was always right - but what
        comes out afterwards is the stop, as itself.
        """
        head = self.ready()
        made = {}
        real_mkstemp = gitgate.tempfile.mkstemp
        real_fsync = os.fsync

        def mkstemp(*args, **kwargs):
            handle, path = real_mkstemp(*args, **kwargs)
            if kwargs.get("prefix") == gitgate.REVIEW_EVIDENCE_PREFIX:
                made["handle"] = handle
                made["path"] = path
            return handle, path

        def fsync_is_interrupted(handle):
            if handle == made.get("handle"):
                raise peer.SignalStop(signal.SIGTERM)
            return real_fsync(handle)

        with mock.patch.object(gitgate.tempfile, "mkstemp", mkstemp):
            with mock.patch("os.fsync", fsync_is_interrupted):
                with self.assertRaises(peer.SignalStop):
                    self.review("accept", self.repo.initial_commit, head)

        self.assertIn("path", made, "no evidence file was ever made")
        self.assertFalse(
            os.path.exists(made["path"]),
            "the partly written evidence survived being stopped",
        )
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.responses(), [])

    def test_a_connector_precheck_runs_inside_the_turn_deadline(self):
        """A connector's own programs are bounded by the turn, not extra to it.

        Two shapes of the same rule. A precheck that hangs is stopped by the
        deadline it was handed, and a builder that comes back after the deadline
        has passed is refused before its command is ever run. Neither publishes
        a request, because nothing was sent.
        """
        head = self.ready()
        for name, build in (
            ("a precheck that hangs", self.precheck_builder(30.0)),
            ("a builder that returns late", self.late_builder()),
        ):
            with self.subTest(builder=name):
                before = len(self.handed)
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        self.session_dir,
                        "claude",
                        REVIEW_BODY,
                        build,
                        2.0,
                        project=self.repo.project,
                        review_base=self.repo.initial_commit,
                        review_head=head,
                    )
                self.assertEqual(caught.exception.failure, Failure.TIMEOUT)
                self.assertEqual(len(self.handed), before + 1)
                self.assertTrue(
                    self.handed[-1].existed,
                    "the evidence was not there when the connector was called",
                )
                self.assertEqual(self.requests(), [])
                self.assertEqual(self.responses(), [])
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

    def test_a_repository_cannot_hide_an_untracked_file(self):
        """Configuration decides what Git mentions. It must not decide this.

        A repository may ask Git to stop reporting untracked files. Set that,
        and an ordinary untracked file - and every ignored one with it -
        disappears from the answer, which this check confirms first so that what
        follows means something. The gate asks for them by name anyway, because
        a reviewer reading the project would see a file no commit contains.
        """
        self.seal(self.repo.initial_commit)
        head = self.repo.add_commit("Ordinary committed work")
        self.repo._git("config", "status.showUntrackedFiles", "no")
        secret = os.path.join(self.repo.project, "secret.txt")
        with open(secret, "w", encoding="utf-8") as stream:
            stream.write("Something no commit contains.\n")

        hidden = self.repo._git("status", "--porcelain", "--ignored")
        self.assertEqual(
            hidden.strip(),
            "",
            "the repository is not hiding anything, so this check proves "
            "nothing",
        )

        error = self.refuse(
            Failure.DIRTY_WORKTREE, "accept", self.repo.initial_commit, head
        )
        self.assertIn("secret.txt", str(error))
        self.assertEqual(self.requests(), [])
        self.assertTrue(
            os.path.exists(secret),
            "an untracked file was deleted instead of being reported",
        )

    def test_a_repository_cannot_hide_a_submodule_it_told_git_to_ignore(self):
        """A whole subdirectory can be made to vanish from the answer.

        A tracked submodule can be marked `ignore = all`, and then modified and
        untracked files inside it stop appearing in `git status` at all - not
        as a changed submodule, not as anything. This check sets that up and
        confirms first that ordinary Git really does report nothing, so what
        follows is not passing for free.

        The gate asks Git to look into submodules regardless of what the
        repository asked for, because a reviewer reading the project would see
        those files and no commit would contain them.

        The submodule's source is a second throwaway repository this check owns
        and deletes with the rest of its fixtures. Nothing reaches the network:
        `protocol.file.allow` is what lets Git clone from a local path at all.
        """
        self.seal(self.repo.initial_commit)
        source = self.new_repository()
        self.repo._git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "--quiet",
            "--",
            source.project,
            "vendor",
        )
        self.repo._git("commit", "--quiet", "-m", "Vendor a second repository")
        head = self.repo.head()

        self.repo._git("config", "submodule.vendor.ignore", "all")
        inside = os.path.join(self.repo.project, "vendor", "smuggled.txt")
        with open(inside, "w", encoding="utf-8") as stream:
            stream.write("Something no commit of either repository holds.\n")

        hidden = self.repo._git(
            "status", "--porcelain", "--untracked-files=all", "--ignored"
        )
        self.assertEqual(
            hidden.strip(),
            "",
            "the submodule is not being hidden, so this check proves nothing",
        )

        error = self.refuse(
            Failure.DIRTY_WORKTREE, "accept", self.repo.initial_commit, head
        )
        self.assertIn("vendor", str(error))
        self.assertEqual(self.requests(), [])
        self.assertTrue(
            os.path.exists(inside),
            "a file inside the submodule was deleted instead of reported",
        )

    def test_a_tracked_file_git_was_told_not_to_look_at_is_refused(self):
        """Two index bits make a changed file invisible. Both are refused.

        `assume-unchanged` and `skip-worktree` both tell Git to stop comparing a
        tracked file with the worktree. Set either one and then change the file,
        and `git status` reports a clean worktree - which this check confirms
        first for each bit in turn, with every switch the gate itself uses, so
        the refusal that follows can only be coming from somewhere else.

        There is no status switch that defeats these, and clearing them would
        mean writing to the index, which a read-only gate must not do. So the
        index is read out instead and the bit is refused wherever it is found.
        The refusal is for the bit being present, not for the file happening to
        differ, because the whole effect of the bit is that Git will not say
        whether it differs.

        Both bits are tried twice: once with the review pointed at the top of
        the repository, and once with it pointed at a subdirectory while the
        concealed file stays at the top. A subdirectory is allowed, because
        identity is settled by resolving the repository's top level, and the
        second half is what proves the index is read from the top: `ls-files`
        reports only what lies below the directory Git was run in unless it is
        told otherwise, so without the `-- :/` pathspec the concealed file is
        outside the answer and the gate opens over a worktree that is not the
        reviewed commit. The control matters more there than anywhere: `git
        status` run in that subdirectory would report an ordinary untracked
        file at the top of the repository, and this one file it cannot see. The
        refusal also has to name the file from the top of the repository rather
        than from wherever the review was pointed, which is what `--full-name`
        is for.
        """
        self.seal(self.repo.initial_commit)
        self.repo.add_commit("Ordinary committed work")
        tracked = synthetic_repo.WRITE_CANARY
        path = os.path.join(self.repo.project, tracked)
        below = os.path.join(self.repo.project, "component")
        os.mkdir(below)
        head = self.repo.add_commit(
            "Committed work in a subdirectory",
            filename=os.path.join("component", "inner.txt"),
        )

        for bit in ("assume-unchanged", "skip-worktree"):
            for pointed_at in (self.repo.project, below):
                with self.subTest(bit=bit, project=pointed_at):
                    self.repo._git("update-index", "--" + bit, "--", tracked)
                    with open(path, "a", encoding="utf-8") as stream:
                        stream.write("A change nothing is meant to notice.\n")

                    hidden = self.plain_git(
                        "status",
                        "--porcelain",
                        "--untracked-files=all",
                        "--ignored",
                        "--ignore-submodules=none",
                        cwd=pointed_at,
                    )
                    self.assertEqual(
                        hidden.returncode,
                        0,
                        hidden.stderr.decode("utf-8", "replace"),
                    )
                    self.assertEqual(
                        hidden.stdout.decode("utf-8", "replace").strip(),
                        "",
                        "the change is not being hidden, so this check proves "
                        "nothing",
                    )

                    error = self.refuse(
                        Failure.DIRTY_WORKTREE,
                        "accept",
                        self.repo.initial_commit,
                        head,
                        project=pointed_at,
                    )
                    self.assertIn(tracked, str(error))
                    self.assertIn(bit, str(error))
                    self.assertIn(
                        ": {0} carries {1}".format(tracked, bit),
                        error.detail,
                        "the concealed file was not named from the top of the "
                        "repository: {0}".format(error.detail),
                    )
                    self.assertEqual(self.requests(), [])

                    self.repo._git(
                        "update-index", "--no-" + bit, "--", tracked
                    )
                    self.repo._git("checkout", "--", tracked)

    def test_the_gate_does_not_fetch_a_missing_object_from_a_remote(self):
        """A partial clone must not make the gate call out to a remote.

        A partial clone keeps only some of its objects and quietly fetches the
        rest from its remote as they are needed - starting an SSH, HTTP,
        remote-helper or credential-helper program to do it. That is an
        external effect, caused by the gate itself, before the peer has even
        been started, so lazy fetching is switched off for every Git command
        the gate runs.

        The order here matters. The gate runs first, against a repository whose
        one interesting object has been deleted: it must fail, and the object
        must still be missing afterwards - which is checked with a probe that
        itself has lazy fetching off, because a probe without it would fetch
        the object and destroy the evidence it was looking for.

        The control comes second, and it is the half that makes the first half
        mean something: the same difference, run as ordinary Git, succeeds and
        leaves the object present. So the remote really was reachable and
        ordinary Git really would have gone to it. The control repairs the
        repository, which is why it cannot come first.

        Nothing reaches the network. The remote is a bare clone of the
        throwaway repository, made inside the directory this check owns.
        """
        self.seal(self.repo.initial_commit)
        head = self.repo.add_commit(
            "Work whose contents this clone will not hold",
            filename="lazy.txt",
            text="The contents that have to be fetched to be diffed.\n",
        )

        source = os.path.join(self.temp, "promisor-source.git")
        cloned = self.plain_git(
            "clone",
            "--bare",
            "--no-hardlinks",
            "--",
            self.repo.project,
            source,
            cwd=self.temp,
        )
        self.assertEqual(
            cloned.returncode, 0, cloned.stderr.decode("utf-8", "replace")
        )
        for name, value in (
            ("uploadpack.allowFilter", "true"),
            ("uploadpack.allowAnySHA1InWant", "true"),
        ):
            configured = self.plain_git(
                "config", name, value, cwd=source
            )
            self.assertEqual(configured.returncode, 0, configured.stderr)

        for name, value in (
            ("core.repositoryformatversion", "1"),
            ("extensions.partialClone", "origin"),
            ("remote.origin.url", source),
            ("remote.origin.promisor", "true"),
            ("remote.origin.partialclonefilter", "blob:none"),
        ):
            self.repo._git("config", name, value)

        blob = self.repo._git("rev-parse", "HEAD:lazy.txt").strip()
        loose = os.path.join(
            self.repo.project, ".git", "objects", blob[:2], blob[2:]
        )
        self.assertTrue(
            os.path.exists(loose),
            "the object is not a loose one, so deleting it proves nothing",
        )
        os.unlink(loose)

        self.refuse(
            Failure.REPOSITORY_UNREADABLE,
            "accept",
            self.repo.initial_commit,
            head,
        )
        self.assertEqual(self.requests(), [])
        still_missing = self.plain_git(
            "cat-file", "-e", blob, env={"GIT_NO_LAZY_FETCH": "1"}
        )
        self.assertNotEqual(
            still_missing.returncode,
            0,
            "the gate fetched the object from the remote",
        )

        control = self.plain_git(
            "--no-pager",
            "diff",
            "{0}..{1}".format(self.repo.initial_commit, head),
        )
        self.assertEqual(
            control.returncode,
            0,
            "ordinary Git could not fetch either, so this check proves "
            "nothing: " + control.stderr.decode("utf-8", "replace"),
        )
        fetched = self.plain_git(
            "cat-file", "-e", blob, env={"GIT_NO_LAZY_FETCH": "1"}
        )
        self.assertEqual(
            fetched.returncode,
            0,
            "ordinary Git did not fetch the object, so this check proves "
            "nothing",
        )

    def test_the_gate_does_not_run_the_repositorys_filesystem_monitor(self):
        """One named way a repository can have its program run, and it cannot.

        Git can be told to start a filesystem-monitor helper while it looks at a
        worktree, and that helper is an ordinary program of the repository's
        choosing. This one leaves a mark. Ordinary Git runs it, which the check
        confirms first; the gate must not, because a supposedly read-only check
        that runs somebody else's program has already had an effect - before the
        peer was even started.

        This proves that one setting, and is named for it. It is not a claim
        that nothing a repository says can make the gate run a program: a
        configured `clean` or `process` content filter still runs during
        `git status`. The repository picks which filter applies through its
        committed attributes, but the command has to be in the user's own
        effective Git configuration already, and does not arrive with a clone.
        That residual is accepted, and INTERFACE.md and `gitgate` both say so.
        """
        head = self.ready()
        marker = os.path.join(self.temp, "the-helper-ran")
        helper = os.path.join(self.temp, "fsmonitor-helper.sh")
        with open(helper, "w", encoding="utf-8") as stream:
            stream.write(FSMONITOR_HELPER.format(marker))
        os.chmod(helper, 0o700)
        self.repo._git("config", "core.fsmonitor", helper)

        self.repo._git("status", "--porcelain")
        self.assertTrue(
            os.path.exists(marker),
            "the helper never ran at all, so this check proves nothing",
        )
        os.unlink(marker)

        result = self.review("accept", self.repo.initial_commit, head)
        self.assertTrue(result.git_unlocked)
        self.assertFalse(
            os.path.exists(marker),
            "the repository chose a program and the gate ran it",
        )
        self.assert_evidence_reached_the_builder(
            self.repo.initial_commit, head
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

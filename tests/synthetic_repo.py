"""A throwaway Git repository to prove a harness stays inside its boundary.

Before a connector is trusted with somebody's real project, it has to be watched
somewhere nothing matters. This fixture builds that place: a small standalone
Git repository holding two marked files, and - just outside it - a decoy folder
holding a value that must never appear in a peer's output.

The shape is the whole point. The repository is what the peer is allowed to
read. The decoy is a sibling, one step outside, reachable by absolute path or by
climbing out with `..`, so a harness that only pretends to confine reads is
caught by evidence rather than by its own assurances. The two canaries make
attempted writes visible: whatever a peer claims, the files either changed or
they did not.

Everything lives under one fresh temporary directory that this fixture creates
itself, and `cleanup()` deletes exactly that directory and nothing else. There
is no glob, no pattern, no path handed in from elsewhere, and no janitor process
that tidies up later.

Nothing here touches the network, a real project, or a real secret. The decoy
value is a random string generated on the spot and used nowhere else.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from typing import List, Optional, Sequence

#: Every temporary parent this fixture creates is named with this prefix, and
#: cleanup refuses to delete a directory that is not.
TEMP_PREFIX = "agent-bridge-synthetic-"

PROJECT_DIRNAME = "project"
DECOY_DIRNAME = "decoy"
DECOY_FILENAME = "decoy-value.txt"

#: A tracked file a peer is permitted to read.
READ_CANARY = "read-canary.txt"
#: A tracked file no peer may change. Its contents are checked afterwards.
WRITE_CANARY = "write-canary.txt"

FIXTURE_NAME = "Agent Bridge Fixture"
FIXTURE_EMAIL = "fixture@agent-bridge.invalid"

#: Settings forced on every Git call so the result does not depend on how this
#: machine happens to be configured.
_GIT_CONFIG: Sequence[str] = (
    "-c",
    "user.name=" + FIXTURE_NAME,
    "-c",
    "user.email=" + FIXTURE_EMAIL,
    "-c",
    "commit.gpgsign=false",
    "-c",
    "init.defaultBranch=main",
    "-c",
    "gc.auto=0",
)


def _git_env() -> dict:
    """The environment every Git call runs in.

    Global and system Git configuration are pointed at an empty device so a
    personal setting - a signing key, a hook path, a default branch name -
    cannot change what this fixture builds. Author and committer identity are
    fixed here as well as on the command line, so a commit succeeds on a machine
    with no Git identity configured at all.
    """
    env = dict(os.environ)
    for inherited in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(inherited, None)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": FIXTURE_NAME,
            "GIT_AUTHOR_EMAIL": FIXTURE_EMAIL,
            "GIT_COMMITTER_NAME": FIXTURE_NAME,
            "GIT_COMMITTER_EMAIL": FIXTURE_EMAIL,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class SyntheticRepo:
    """One disposable repository, its decoy neighbour, and how to remove them.

    Attributes worth knowing:

    - `parent` - the one temporary directory this fixture created and the only
      thing `cleanup()` will ever delete.
    - `project` - the Git repository a peer may be given read access to.
    - `decoy_dir` and `decoy_file` - the sibling that must stay unreachable.
    - `decoy_value` - the unique string that must never appear in peer output.
    - `initial_commit` - the full commit id of the first commit.
    """

    def __init__(self, parent: str) -> None:
        self.parent = parent
        self.project = os.path.join(parent, PROJECT_DIRNAME)
        self.decoy_dir = os.path.join(parent, DECOY_DIRNAME)
        self.decoy_file = os.path.join(self.decoy_dir, DECOY_FILENAME)
        self.decoy_value = "AGENT-BRIDGE-SYNTHETIC-DECOY-" + uuid.uuid4().hex
        self.initial_commit = ""
        self._commit_count = 0
        self._removed = False

    # -- Git ---------------------------------------------------------------

    def _git(self, *args: str) -> str:
        """Run one Git command in the project, as a fixed argument vector.

        There is no shell anywhere in this path: the arguments are passed to the
        program exactly as written here.
        """
        command: List[str] = ["git", "-C", self.project]
        command.extend(_GIT_CONFIG)
        command.extend(args)
        completed = subprocess.run(
            command,
            cwd=self.project,
            env=_git_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "git {0} failed ({1}): {2}".format(
                    " ".join(args),
                    completed.returncode,
                    completed.stderr.decode("utf-8", "replace").strip(),
                )
            )
        return completed.stdout.decode("utf-8", "replace")

    def head(self) -> str:
        """The full commit id the repository is currently on."""
        return self._git("rev-parse", "HEAD").strip()

    def add_commit(
        self,
        message: str = "Synthetic change",
        filename: Optional[str] = None,
        text: Optional[str] = None,
    ) -> str:
        """Add one tracked file and commit it. Returns the new commit id."""
        self._commit_count += 1
        if filename is None:
            filename = "change-{0}.txt".format(self._commit_count)
        if text is None:
            text = "Synthetic change {0}.\n".format(self._commit_count)
        _write(os.path.join(self.project, filename), text)
        self._git("add", "--", filename)
        self._git("commit", "--quiet", "-m", message)
        return self.head()

    def make_dirty(self) -> str:
        """Change a tracked file without committing. Returns its path.

        This is how a check produces a worktree that has no exact committed head
        to review.
        """
        path = os.path.join(self.project, WRITE_CANARY)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("Uncommitted synthetic edit.\n")
        return path

    # -- Removal -----------------------------------------------------------

    def cleanup(self) -> None:
        """Delete the exact temporary directory this fixture created.

        It takes no argument on purpose. The only path it can ever remove is the
        one recorded when the fixture was built, it must be an absolute path
        below the filesystem root, and it must carry this fixture's prefix. A
        failure to remove it is raised rather than swallowed, because a
        temporary repository left behind is something a person needs to know
        about.
        """
        if self._removed:
            return
        parent = self.parent
        if not os.path.isabs(parent) or os.path.dirname(parent) == parent:
            raise RuntimeError(
                "refusing to remove {0!r}: not a directory this fixture "
                "created".format(parent)
            )
        if not os.path.basename(parent).startswith(TEMP_PREFIX):
            raise RuntimeError(
                "refusing to remove {0!r}: not a directory this fixture "
                "created".format(parent)
            )
        if os.path.exists(parent):
            shutil.rmtree(parent)
        self._removed = True


def create(parent_dir: Optional[str] = None) -> SyntheticRepo:
    """Build one disposable repository and its decoy neighbour.

    A fresh private directory is always made for this fixture's own use. With no
    argument it is made in the system temporary area; `parent_dir` places it
    inside a temporary directory the caller already owns. Either way the
    directory is new, is readable only by this account, and is the only thing
    `cleanup()` can delete. The caller is responsible for keeping that location
    outside the bridge repository and outside every real project.
    """
    parent = os.path.realpath(
        tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=parent_dir)
    )
    repo = SyntheticRepo(parent)

    os.mkdir(repo.project)
    os.mkdir(repo.decoy_dir)

    _write(
        repo.decoy_file,
        "This file is outside the project and must never be read.\n"
        + repo.decoy_value
        + "\n",
    )

    _write(
        os.path.join(repo.project, "README.md"),
        "# Synthetic project\n\nDisposable fixture. Nothing here is real.\n",
    )
    _write(
        os.path.join(repo.project, READ_CANARY),
        "Readable by a peer under the approved project root.\n",
    )
    _write(
        os.path.join(repo.project, WRITE_CANARY),
        "This content must be unchanged after a peer has run.\n",
    )

    repo._git("init", "--quiet")
    repo._git("add", "--", "README.md", READ_CANARY, WRITE_CANARY)
    repo._git("commit", "--quiet", "-m", "Initial synthetic commit")
    repo.initial_commit = repo.head()
    return repo

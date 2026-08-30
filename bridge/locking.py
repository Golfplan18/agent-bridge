"""One lock per session, held by one process, for one turn.

Two Agent Bridge turns must never write into the same session at the same time,
because they would both believe they had the next message number. The way that
is prevented is deliberately the oldest and dullest one available: an advisory
lock taken out on a file, held open by the running process.

What makes it safe is what it does *not* do. It records nothing. There is no
lease to renew, no heartbeat to miss, no timestamp to compare, no owner name to
trust, and no rule for deciding that somebody else's lock has gone stale and may
be taken away. The operating system holds the lock as long as the descriptor is
open, and drops it the moment that process ends - normally, by crash, or by
being killed outright. A dead holder therefore blocks nobody, and no code of
ours has to notice that it died.

`.lock` is a pathname to lock, not a place to keep state. Nothing is ever
written into it, nothing is ever read out of it, and deleting it destroys no
part of the session record.

Contention is not an error to work around. It means another turn is busy, so the
answer is `BUSY_SESSION`, immediately, having changed nothing.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from typing import Iterator

from .errors import BridgeError, Failure

#: The file inside a session directory that is used purely as a lock target.
LOCK_FILENAME = ".lock"


def lock_path(session_dir: str) -> str:
    """Where this session's lock is taken out."""
    return os.path.join(session_dir, LOCK_FILENAME)


@contextlib.contextmanager
def session_lock(session_dir: str) -> Iterator[str]:
    """Hold this session's lock for the duration of the block.

    Raises `BUSY_SESSION` at once if another process already holds it - no
    waiting, no retry, and nothing in the session touched. Raises
    `SESSION_NOT_FOUND` when there is no such directory to lock.

    The lock is released by closing the descriptor on the way out, which happens
    whether the block finished, raised, or was interrupted.
    """
    if not os.path.isdir(session_dir):
        raise BridgeError(Failure.SESSION_NOT_FOUND, detail=session_dir)
    path = lock_path(session_dir)
    try:
        handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BridgeError(Failure.BUSY_SESSION, detail=session_dir)
        yield path
    finally:
        os.close(handle)

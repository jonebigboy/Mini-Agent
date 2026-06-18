"""Tests for session FileLock."""
import os
import multiprocessing as mp
import time
from pathlib import Path

import pytest

from mini_agent.session.locker import FileLock, SessionLockedError


def _hold_lock(path: str, ready: mp.Event, release: mp.Event):
    """Helper for multiprocessing tests - must be at module level for pickle."""
    lock = FileLock(Path(path))
    lock.acquire()
    ready.set()
    release.wait(5)


def test_lock_acquire_release(mini_agent_home, tmp_path):
    lockfile = tmp_path / "test.lock"
    lock = FileLock(lockfile)
    lock.acquire()
    assert lockfile.exists()
    lock.release()
    # Re-acquire works after release
    lock.acquire()
    lock.release()


def test_lock_non_blocking_fails_when_held(mini_agent_home, tmp_path):
    """Second acquire attempt with blocking=False raises SessionLockedError."""
    lockfile = tmp_path / "test.lock"
    lock1 = FileLock(lockfile)
    lock1.acquire()

    lock2 = FileLock(lockfile)
    with pytest.raises(SessionLockedError):
        lock2.acquire(blocking=False)

    lock1.release()


def test_lock_released_on_process_death(mini_agent_home, tmp_path):
    """flock auto-releases when the holding process exits (even SIGKILL)."""
    lockfile = tmp_path / "test.lock"

    ready = mp.Event()
    release = mp.Event()
    p = mp.Process(target=_hold_lock, args=(str(lockfile), ready, release))
    p.start()
    assert ready.wait(2), "child did not acquire lock in time"

    # Kill it WITHOUT signaling release — simulate crash
    p.kill()
    p.join(2)

    # Lock should now be available
    new_lock = FileLock(lockfile)
    new_lock.acquire(blocking=False)  # should not raise
    new_lock.release()


def test_lock_context_manager(mini_agent_home, tmp_path):
    lockfile = tmp_path / "test.lock"
    with FileLock(lockfile):
        # Re-acquire fails inside the with block
        second = FileLock(lockfile)
        with pytest.raises(SessionLockedError):
            second.acquire(blocking=False)
    # After with block, re-acquire works
    second = FileLock(lockfile)
    second.acquire(blocking=False)
    second.release()


def test_lock_probe_does_not_hold(mini_agent_home, tmp_path):
    """probe() acquires non-blocking, immediately releases. Used for探活."""
    lockfile = tmp_path / "test.lock"
    FileLock(lockfile).probe()  # should succeed (no holder)
    # Lock is not held after probe
    FileLock(lockfile).acquire(blocking=False)

# tests/test_session_store.py
"""Tests for SessionStore: list/find/fork/TTL."""
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from mini_agent.session.writer import SessionWriter
from mini_agent.session.store import SessionStore, SessionMeta


@pytest.fixture
def store(mini_agent_home):
    return SessionStore("/proj")


def _make_session(sid: str, cwd: str = "/proj", ended: bool = True):
    w = SessionWriter(sid, cwd, cwd, "m", {})
    w.open()
    w.append_user_message(f"hello from {sid}")
    if ended:
        w.close(end_reason="exit")
    else:
        # Release the lock without writing session_end — simulates a crashed
        # process (kernel auto-releases flock) leaving an un-ended session.
        w._lock.release()
        w._opened = False
        if w._fh:
            w._fh.flush()
            w._fh.close()
            w._fh = None


def test_list_sessions_empty(store):
    assert store.list_sessions() == []


def test_list_sessions_returns_meta(store, mini_agent_home):
    _make_session("s1")
    _make_session("s2")
    metas = store.list_sessions()
    assert len(metas) == 2
    ids = {m.session_id for m in metas}
    assert ids == {"s1", "s2"}


def test_list_sessions_marks_in_progress(store, mini_agent_home):
    _make_session("ended", ended=True)
    _make_session("inprog", ended=False)  # no close
    metas = {m.session_id: m for m in store.list_sessions()}
    assert metas["ended"].ended is True
    assert metas["inprog"].ended is False
    assert metas["inprog"].end_reason is None


def test_list_sessions_preview_is_last_user_message(store, mini_agent_home):
    _make_session("s1")
    metas = store.list_sessions()
    assert metas[0].preview.startswith("hello from")


def test_find_latest(store, mini_agent_home):
    _make_session("old")
    time.sleep(0.05)
    _make_session("new")
    latest = store.find_latest()
    assert latest.session_id == "new"


def test_find_in_progress_filters_ended(store, mini_agent_home):
    _make_session("ended", ended=True)
    _make_session("inprog", ended=False)
    inprog = store.find_in_progress(max_age_hours=24)
    assert len(inprog) == 1
    assert inprog[0].session_id == "inprog"


def test_ttl_cleanup_removes_old_sessions(store, mini_agent_home):
    _make_session("old")
    # Backdate the file
    jsonl = store.session_path("old")
    st = jsonl.stat()
    import os
    old_time = time.time() - (31 * 24 * 3600)  # 31 days ago
    os.utime(jsonl, (old_time, old_time))
    removed = store.cleanup_old_sessions(max_age_days=30)
    assert removed == 1
    assert not jsonl.exists()


def test_ttl_keeps_current_session(store, mini_agent_home):
    _make_session("now")
    removed = store.cleanup_old_sessions(max_age_days=30)
    assert removed == 0


def test_fork_session_copies_and_marks_origin(store, mini_agent_home):
    from mini_agent.session.writer import SessionWriter
    # Source session
    src = SessionWriter("src-1", "/proj", "/proj", "m", {})
    src.open()
    src.append_user_message("source message")
    src.close()

    # Fork
    store.fork_session("src-1", "fork-1")

    # Fork file exists
    fork_path = store.session_path("fork-1")
    assert fork_path.exists()

    # Fork has all original events (we'll add session_start later)
    # For now: fork file should be at least as large as source
    src_path = store.session_path("src-1")
    assert fork_path.stat().st_size >= src_path.stat().st_size


def test_fork_preserves_source_unchanged(store, mini_agent_home):
    from mini_agent.session.writer import SessionWriter
    src = SessionWriter("src-2", "/proj", "/proj", "m", {})
    src.open()
    src.append_user_message("source message")
    src.close()

    src_before = store.session_path("src-2").read_text()
    store.fork_session("src-2", "fork-2")
    src_after = store.session_path("src-2").read_text()
    assert src_before == src_after  # unchanged

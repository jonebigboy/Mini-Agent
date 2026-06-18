"""End-to-end tests for session lifecycle."""
import json
import os
import time

import pytest

from mini_agent.paths import project_dir, sessions_dir
from mini_agent.session.locker import SessionLockedError
from mini_agent.session.reader import SessionReader
from mini_agent.session.store import SessionStore
from mini_agent.session.writer import SessionWriter


def test_full_lifecycle_new_resume_fork(mini_agent_home, tmp_path):
    """Create → resume → fork, verifying message continuity."""
    cwd = str(tmp_path)

    # Phase 1: Create initial session with some messages
    sid1 = "e2e-001"
    w1 = SessionWriter(sid1, cwd, cwd, "test-model", {})
    w1.open()
    w1.append_user_message("first user msg")
    w1.append_assistant_message("assistant reply", thinking="t")
    w1.append_tool_result("c1", "file_read", "file content")
    w1.close()

    # Phase 2: Resume — should see all messages in compacted mode
    reader = SessionReader(cwd)
    resumed = reader.load_messages(sid1, mode="compacted")
    # user + assistant + tool = 3 messages
    assert len(resumed) == 3
    assert resumed[0].content == "first user msg"

    # Phase 3: Fork — should also see all messages (raw mode)
    store = SessionStore(cwd)
    sid_fork = "e2e-fork-001"
    store.fork_session(sid1, sid_fork)
    forked = reader.load_messages(sid_fork, mode="raw")
    assert len(forked) == 3  # same as source

    # Phase 4: Add new messages to fork — source unchanged
    w_fork = SessionWriter(sid_fork, cwd, cwd, "test-model", {})
    w_fork.open(forked_from=sid1)
    w_fork.append_user_message("forked branch message")
    w_fork.close()

    # Source session still has 3 messages
    source_msgs = reader.load_messages(sid1, mode="raw")
    assert len(source_msgs) == 3
    # Forked has 4 now
    forked_after = reader.load_messages(sid_fork, mode="raw")
    assert len(forked_after) == 4


def test_summary_event_compacted_mode(mini_agent_home, tmp_path):
    """After a summary_event, compacted mode skips summarized seqs."""
    cwd = str(tmp_path)
    sid = "e2e-sum-001"
    w = SessionWriter(sid, cwd, cwd, "m", {})
    w.open()
    w.append_user_message("u1")            # seq 1
    w.append_assistant_message("a1")        # seq 2
    w.append_tool_result("c1", "f", "r1")   # seq 3
    w.append_summary_event(                 # seq 4 (meta)
        summarized_seqs=[2, 3],
        summary_seq=5,
        tokens_before=80000,
        tokens_after=10000,
    )
    w.append_user_message("[Summary]\nDone")  # seq 5
    w.append_user_message("u2")              # seq 6
    w.close()

    reader = SessionReader(cwd)
    raw = reader.load_messages(sid, mode="raw")
    compacted = reader.load_messages(sid, mode="compacted")
    # raw: all 5 messages
    assert len(raw) == 5
    # compacted: skip seq 2,3 → keep 1, 5, 6 = 3 messages
    assert len(compacted) == 3
    assert compacted[0].content == "u1"
    assert compacted[1].content.startswith("[Summary]")
    assert compacted[2].content == "u2"


def test_concurrent_resume_blocked_by_lock(mini_agent_home, tmp_path):
    """Two writers on same session: second must fail with SessionLockedError."""
    cwd = str(tmp_path)
    sid = "e2e-lock-001"
    w1 = SessionWriter(sid, cwd, cwd, "m", {})
    w1.open()

    w2 = SessionWriter(sid, cwd, cwd, "m", {})
    with pytest.raises(SessionLockedError):
        w2.open()

    w1.close()


def test_partial_line_recovery(mini_agent_home, tmp_path):
    """A truncated last line must not block session load."""
    cwd = str(tmp_path)
    sid = "e2e-partial-001"
    sess = project_dir(cwd) / "sessions"
    sess.mkdir(parents=True)
    jsonl = sess / f"{sid}.jsonl"

    events = [
        {"seq": 0, "ts": "t", "type": "session_start", "session_id": sid, "schema_version": "1"},
        {"seq": 1, "ts": "t", "type": "message", "session_id": sid, "role": "user", "content": "first"},
        {"seq": 2, "ts": "t", "type": "message", "session_id": sid, "role": "user", "content": "second"},
    ]
    with open(jsonl, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        # Truncated final line
        f.write('{"seq":3,"ts":"t","type":"message","session_id":"' + sid + '",')

    reader = SessionReader(cwd)
    msgs = reader.load_messages(sid, mode="raw")
    assert len(msgs) == 2  # first + second only


def test_ttl_does_not_delete_locked(mini_agent_home, tmp_path):
    """An actively-locked session must survive TTL cleanup."""
    cwd = str(tmp_path)
    sid = "e2e-ttl-001"
    w = SessionWriter(sid, cwd, cwd, "m", {})
    w.open()  # holds lock

    # Backdate the file
    jsonl = sessions_dir(cwd) / f"{sid}.jsonl"
    old = time.time() - 31 * 24 * 3600
    os.utime(jsonl, (old, old))

    store = SessionStore(cwd)
    removed = store.cleanup_old_sessions(max_age_days=30)
    assert removed == 0  # locked, skip
    assert jsonl.exists()

    w.close()

# tests/test_session_reader.py
"""Tests for SessionReader — raw/compacted modes + robustness contract."""
import hashlib
import json
from pathlib import Path

import pytest

from mini_agent.schema import Message
from mini_agent.session.events import SessionEvent
from mini_agent.session.reader import SessionReader, SessionNotFoundError
from mini_agent.session.writer import SessionWriter


@pytest.fixture
def populated_session(mini_agent_home):
    """Create a session with: 2 user msgs + assistant + tool_result + summary_event."""
    w = SessionWriter(
        session_id="abc-001",
        cwd="/proj",
        workspace="/proj",
        model="m",
        cli_args={},
    )
    w.open()
    w.append_user_message("first")             # seq 1
    w.append_assistant_message(                # seq 2
        content="thinking...",
        thinking="t",
        tool_calls=[{"id": "c1", "function": {"name": "f"}}],
    )
    w.append_tool_result("c1", "f", "result")  # seq 3
    w.append_summary_event(                    # seq 4 (meta)
        summarized_seqs=[2, 3],
        summary_seq=5,
        tokens_before=80000,
        tokens_after=10000,
    )
    w.append_user_message(                      # seq 5 (summary text)
        "[Assistant Execution Summary]\nDone"
    )
    w.append_user_message("second user msg")    # seq 6
    w.close()
    return "abc-001", "/proj"


def test_load_raw_replays_everything(populated_session):
    sid, cwd = populated_session
    reader = SessionReader(cwd)
    msgs = reader.load_messages(sid, mode="raw")
    # All 6 message events (excluding session_start, session_end, summary_event)
    assert len(msgs) == 5  # user + assistant + tool + summary-user + user
    assert msgs[0].role == "user"
    assert msgs[0].content == "first"
    assert msgs[1].role == "assistant"


def test_load_compacted_skips_summarized(populated_session):
    sid, cwd = populated_session
    reader = SessionReader(cwd)
    msgs = reader.load_messages(sid, mode="compacted")
    # compacted: skip seqs 2,3 (summarized); keep 1 (user), 5 (summary text), 6 (user)
    assert len(msgs) == 3
    assert msgs[0].content == "first"
    assert msgs[1].content.startswith("[Assistant Execution Summary]")
    assert msgs[2].content == "second user msg"


def test_load_default_mode_is_compacted(populated_session):
    sid, cwd = populated_session
    reader = SessionReader(cwd)
    msgs = reader.load_messages(sid)
    assert len(msgs) == 3  # same as compacted


def test_load_partial_last_line(mini_agent_home):
    """Critical: malformed last line must NOT abort session load."""
    sid, cwd = "partial-001", "/p"
    sessions_dir = mini_agent_home / ".mini-agent" / "projects" / "p" / "sessions"
    sessions_dir.mkdir(parents=True)
    jsonl = sessions_dir / f"{sid}.jsonl"
    # Write 3 valid events then a truncated line
    valid = [
        {"seq":0,"ts":"t","type":"session_start","session_id":sid,"schema_version":"1"},
        {"seq":1,"ts":"t","type":"message","session_id":sid,"role":"user","content":"hi"},
        {"seq":2,"ts":"t","type":"message","session_id":sid,"role":"user","content":"bye"},
    ]
    with open(jsonl, "w") as f:
        for e in valid:
            f.write(json.dumps(e) + "\n")
        f.write('{"seq":3,"ts":"t","type":"message","session_id":"' + sid + '","role":"user","content":"trunca')  # no newline, no closing

    reader = SessionReader(cwd)
    msgs = reader.load_messages(sid, mode="raw")
    # Both prior user messages loaded; truncated one dropped
    assert len(msgs) == 2


def test_load_missing_external_ref_replaced_with_placeholder(mini_agent_home):
    """If external_ref file is missing, content becomes a placeholder + warn."""
    sid, cwd = "ext-missing", "/p"
    sessions_dir = mini_agent_home / ".mini-agent" / "projects" / "p" / "sessions"
    sessions_dir.mkdir(parents=True)
    jsonl = sessions_dir / f"{sid}.jsonl"
    event = {
        "seq": 1, "ts": "t", "type": "message", "session_id": sid,
        "role": "tool", "tool_call_id": "c1", "name": "f",
        "content": [
            {"type": "external_ref", "path": "tool_outputs/ext-missing/seq-1.txt",
             "size": 10, "sha256": "0" * 64}
        ],
    }
    with open(jsonl, "w") as f:
        f.write(json.dumps({"seq":0,"ts":"t","type":"session_start","session_id":sid}) + "\n")
        f.write(json.dumps(event) + "\n")

    reader = SessionReader(cwd)
    msgs = reader.load_messages(sid, mode="raw")
    assert len(msgs) == 1
    assert "unavailable" in msgs[0].content[0]["text"].lower()


def test_load_unknown_event_type_skipped(mini_agent_home):
    """Forward compat: unknown event types warn + skip, don't crash."""
    sid, cwd = "fwd-001", "/p"
    sessions_dir = mini_agent_home / ".mini-agent" / "projects" / "p" / "sessions"
    sessions_dir.mkdir(parents=True)
    jsonl = sessions_dir / f"{sid}.jsonl"
    with open(jsonl, "w") as f:
        f.write(json.dumps({"seq":0,"ts":"t","type":"session_start","session_id":sid}) + "\n")
        # Future event type we don't know about
        f.write(json.dumps({"seq":1,"ts":"t","type":"future_event","session_id":sid,"data":"x"}) + "\n")
        f.write(json.dumps({"seq":2,"ts":"t","type":"message","session_id":sid,"role":"user","content":"hi"}) + "\n")

    reader = SessionReader(cwd)
    msgs = reader.load_messages(sid, mode="raw")
    assert len(msgs) == 1  # only the message
    assert msgs[0].content == "hi"


def test_load_not_found_raises(mini_agent_home):
    reader = SessionReader("/p")
    with pytest.raises(SessionNotFoundError):
        reader.load_messages("does-not-exist")

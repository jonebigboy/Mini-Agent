# tests/test_session_events.py
"""Tests for SessionEvent schema."""
import json
import pytest
from pydantic import ValidationError

from mini_agent.session.events import SessionEvent, ExternalRefBlock


def test_external_ref_block_valid():
    block = ExternalRefBlock(
        path="tool_outputs/abc/seq-3.txt",
        size=12345,
        sha256="a" * 64,
    )
    assert block.type == "external_ref"
    assert block.size == 12345


def test_external_ref_block_invalid_sha256():
    with pytest.raises(ValidationError):
        ExternalRefBlock(path="x", size=10, sha256="short")


def test_session_event_session_start_minimal():
    e = SessionEvent(
        seq=0,
        ts="2026-06-18T10:00:00.000Z",
        type="session_start",
        session_id="abc-123",
        schema_version="1",
    )
    assert e.role is None
    assert e.content is None


def test_session_event_message_user():
    e = SessionEvent(
        seq=1,
        ts="2026-06-18T10:00:01.000Z",
        type="message",
        session_id="abc-123",
        role="user",
        content="hello",
    )
    assert e.role == "user"


def test_session_event_session_end_with_reason():
    e = SessionEvent(
        seq=10,
        ts="2026-06-18T11:00:00.000Z",
        type="session_end",
        session_id="abc-123",
        end_reason="exit",
    )
    assert e.end_reason == "exit"


def test_session_event_summary_event():
    e = SessionEvent(
        seq=99,
        ts="2026-06-18T10:30:00.000Z",
        type="summary_event",
        session_id="abc-123",
        summarized_seqs=[2, 3, 4],
        summary_seq=100,
        tokens_before=85000,
        tokens_after=12000,
    )
    assert e.summarized_seqs == [2, 3, 4]


def test_session_event_json_roundtrip():
    """Event must serialize to JSON and back losslessly (for JSONL storage)."""
    original = SessionEvent(
        seq=5,
        ts="2026-06-18T10:00:00.000Z",
        type="message",
        session_id="abc",
        role="tool",
        tool_call_id="call_1",
        name="file_read",
        content=[
            {"type": "text", "text": "see file:"},
            {"type": "external_ref", "path": "x", "size": 10, "sha256": "b" * 64},
        ],
    )
    dumped = original.model_dump_json()
    restored = SessionEvent.model_validate_json(dumped)
    assert restored.seq == 5
    assert restored.role == "tool"
    assert len(restored.content) == 2


def test_session_event_unknown_type_rejected():
    """Unknown event types fail validation at write time (only reader tolerates them)."""
    with pytest.raises(ValidationError):
        SessionEvent(seq=0, ts="x", type="bogus", session_id="abc")

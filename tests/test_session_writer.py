# tests/test_session_writer.py
"""Tests for SessionWriter."""
import json
from pathlib import Path

import pytest

from mini_agent.session.writer import SessionWriter
from mini_agent.session.events import SessionEvent


@pytest.fixture
def writer(mini_agent_home):
    w = SessionWriter(
        session_id="test-sid-001",
        cwd="/Users/test/proj",
        workspace="/Users/test/proj",
        model="test-model",
        cli_args={"mode": "interactive"},
    )
    w.open()
    yield w
    try:
        w.close(end_reason="exit")
    except Exception:
        pass


def test_open_writes_session_start(writer, mini_agent_home):
    jsonl = mini_agent_home / ".mini-agent" / "projects" / "Users-test-proj" / "sessions" / "test-sid-001.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text().strip().split("\n")
    assert len(lines) == 1
    event = SessionEvent.model_validate_json(lines[0])
    assert event.type == "session_start"
    assert event.schema_version == "1"
    assert event.cwd == "/Users/test/proj"
    assert event.model == "test-model"
    assert event.seq == 0


def test_open_redacts_secrets_in_cli_args(mini_agent_home):
    w = SessionWriter(
        session_id="test-sid-secret",
        cwd="/Users/test/p",
        workspace="/Users/test/p",
        model="m",
        cli_args={
            "mode": "task",
            "api_key": "sk-secret-123",
            "token": "tok-abc",
            "password": "pw",
            "normal_value": "keep-me",
            "nested": {"secret_api": "hidden", "ok": "visible"},
        },
    )
    w.open()
    jsonl = (
        mini_agent_home / ".mini-agent" / "projects" / "Users-test-p"
        / "sessions" / "test-sid-secret.jsonl"
    )
    event = SessionEvent.model_validate_json(jsonl.read_text().strip())
    assert event.cli_args["api_key"] == "***"
    assert event.cli_args["token"] == "***"
    assert event.cli_args["password"] == "***"
    assert event.cli_args["normal_value"] == "keep-me"
    assert event.cli_args["nested"]["secret_api"] == "***"
    assert event.cli_args["nested"]["ok"] == "visible"
    w.close()


def test_append_user_message_seq_monotonic(writer):
    seq1 = writer.append_user_message("hello")
    seq2 = writer.append_user_message("world")
    assert seq1 == 1
    assert seq2 == 2


def test_append_assistant_message(writer):
    seq = writer.append_assistant_message(
        content="thinking about it",
        thinking="internal",
        tool_calls=[{"id": "call_1", "type": "function",
                     "function": {"name": "file_read", "arguments": "{}"}}],
    )
    assert seq == 1


def test_append_tool_result(writer):
    seq = writer.append_tool_result(
        tool_call_id="call_1",
        name="file_read",
        content="file contents here",
    )
    assert seq == 1


def test_append_summary_event(writer):
    seq = writer.append_summary_event(
        summarized_seqs=[2, 3],
        summary_seq=5,
        tokens_before=85000,
        tokens_after=12000,
    )
    assert seq == 1


def test_close_writes_session_end(writer, mini_agent_home):
    writer.close(end_reason="exit")
    jsonl = (
        mini_agent_home / ".mini-agent" / "projects" / "Users-test-proj"
        / "sessions" / "test-sid-001.jsonl"
    )
    lines = jsonl.read_text().strip().split("\n")
    last = SessionEvent.model_validate_json(lines[-1])
    assert last.type == "session_end"
    assert last.end_reason == "exit"


def test_each_append_persists_immediately(writer, mini_agent_home):
    """Verify fsync + append: every append is on disk before call returns."""
    writer.append_user_message("first")
    writer.append_user_message("second")
    jsonl = (
        mini_agent_home / ".mini-agent" / "projects" / "Users-test-proj"
        / "sessions" / "test-sid-001.jsonl"
    )
    lines = jsonl.read_text().strip().split("\n")
    assert len(lines) == 3  # session_start + 2 user messages


def test_tool_result_large_content_externalized(mini_agent_home):
    """Text blocks > 10 KB are written to tool_outputs/ and referenced by sha256."""
    from mini_agent.session.writer import SessionWriter
    w = SessionWriter(
        session_id="sid-ext",
        cwd="/p",
        workspace="/p",
        model="m",
        cli_args={},
    )
    w.open()
    big_text = "x" * (10 * 1024 + 100)
    seq = w.append_tool_result(
        tool_call_id="c1",
        name="file_read",
        content=[{"type": "text", "text": big_text}],
    )
    jsonl = (
        mini_agent_home / ".mini-agent" / "projects" / "p" / "sessions" / "sid-ext.jsonl"
    )
    lines = jsonl.read_text().strip().split("\n")
    tool_event = SessionEvent.model_validate_json(lines[-1])
    assert tool_event.seq == seq
    assert len(tool_event.content) == 1
    block = tool_event.content[0]
    assert block["type"] == "external_ref"
    assert block["size"] == len(big_text.encode("utf-8"))
    # The actual file exists
    external_path = (
        mini_agent_home / ".mini-agent" / "projects" / "p"
        / block["path"]
    )
    assert external_path.exists()
    assert external_path.read_text() == big_text
    w.close()


def test_tool_result_small_content_inline(mini_agent_home):
    """Small text blocks stay inline."""
    from mini_agent.session.writer import SessionWriter
    w = SessionWriter(
        session_id="sid-small",
        cwd="/p",
        workspace="/p",
        model="m",
        cli_args={},
    )
    w.open()
    w.append_tool_result(
        tool_call_id="c1",
        name="echo",
        content=[{"type": "text", "text": "small"}],
    )
    jsonl = (
        mini_agent_home / ".mini-agent" / "projects" / "p" / "sessions" / "sid-small.jsonl"
    )
    lines = jsonl.read_text().strip().split("\n")
    tool_event = SessionEvent.model_validate_json(lines[-1])
    assert tool_event.content == [{"type": "text", "text": "small"}]
    w.close()


def test_tool_result_mixed_blocks(mini_agent_home):
    """A single tool_result with [text, big-text] becomes [text, external_ref]."""
    from mini_agent.session.writer import SessionWriter
    w = SessionWriter(
        session_id="sid-mix",
        cwd="/p",
        workspace="/p",
        model="m",
        cli_args={},
    )
    w.open()
    w.append_tool_result(
        tool_call_id="c1",
        name="bash",
        content=[
            {"type": "text", "text": "Output:\n"},
            {"type": "text", "text": "y" * (10 * 1024 + 1)},
        ],
    )
    jsonl = (
        mini_agent_home / ".mini-agent" / "projects" / "p" / "sessions" / "sid-mix.jsonl"
    )
    lines = jsonl.read_text().strip().split("\n")
    tool_event = SessionEvent.model_validate_json(lines[-1])
    assert tool_event.content[0] == {"type": "text", "text": "Output:\n"}
    assert tool_event.content[1]["type"] == "external_ref"
    w.close()

"""End-to-end: new/resume/fork branches in cli.py main flow."""
from pathlib import Path
from unittest.mock import patch

import pytest

from mini_agent.session.writer import SessionWriter


class MockArgs:
    """Minimal args mock for main() bypass."""
    def __init__(self, **kw):
        # Set defaults for ALL attributes main() / run_agent() may read
        defaults = {
            "workspace": None,
            "task": None,
            "command": None,
            "filename": None,
            "continue_latest": False,
            "resume_id": None,
            "fork_id": None,
            "any_workspace": False,
            "session_command": None,
            "session_action": None,
        }
        defaults.update(kw)
        self.__dict__.update(defaults)


def test_cli_resume_loads_compacted_messages(mini_agent_home, tmp_path):
    """When --resume is given, Agent.messages should be loaded from JSONL.

    Mocks parse_args + run_repl so we just verify the branching wires
    the reader output into Agent.messages before run_repl is called.
    """
    # Create a session to resume (in tmp_path-as-cwd)
    sid = "resumable-001"
    w = SessionWriter(sid, str(tmp_path), str(tmp_path), "test-model", {})
    w.open()
    w.append_user_message("first msg")
    w.append_assistant_message("reply", thinking="t")
    w.close()

    captured = {}

    def capture_repl(agent, session_writer, *args, **kwargs):
        captured["messages"] = list(agent.messages)
        captured["writer"] = session_writer

    with patch("mini_agent.cli.parse_args") as mock_parse, \
         patch("mini_agent.cli.run_repl", side_effect=capture_repl), \
         patch("mini_agent.cli.Path") as mock_path_cls:
        mock_parse.return_value = MockArgs(resume_id=sid)
        # Make Path.cwd() return tmp_path so reader/store find the session
        mock_path_cls.cwd.return_value = tmp_path
        mock_path_cls.home = Path.home  # don't break other Path.home() calls
        # Allow Path(workspace).expanduser().absolute() to still work via real Path
        mock_path_cls.side_effect = lambda p: Path(p)

        from mini_agent.cli import main
        try:
            main()
        except SystemExit:
            pass

    assert "messages" in captured, "run_repl was not called — branching failed"
    # system + user + assistant = 3 messages minimum
    assert len(captured["messages"]) >= 3
    user_msgs = [m for m in captured["messages"] if m.role == "user"]
    assert any("first msg" in (m.content or "") for m in user_msgs)


def test_resolve_session_prefix_matches_uuid_prefix(mini_agent_home, tmp_path):
    """resolve_session_prefix returns full sid for unique prefix."""
    from mini_agent.cli import resolve_session_prefix
    from mini_agent.session.store import SessionStore

    sid = "abc123def459"
    w = SessionWriter(sid, str(tmp_path), str(tmp_path), "m", {})
    w.open()
    w.append_user_message("x")
    w.close()

    store = SessionStore(str(tmp_path))
    resolved = resolve_session_prefix("abc123", store, any_workspace=False)
    assert resolved == sid


def test_resolve_session_prefix_ambiguous_raises(mini_agent_home, tmp_path):
    """Ambiguous prefix raises SessionNotFoundError."""
    from mini_agent.cli import resolve_session_prefix
    from mini_agent.session.store import SessionStore
    from mini_agent.session.reader import SessionNotFoundError

    for sid in ("abc123aaa", "abc123bbb"):
        w = SessionWriter(sid, str(tmp_path), str(tmp_path), "m", {})
        w.open()
        w.append_user_message("x")
        w.close()

    store = SessionStore(str(tmp_path))
    with pytest.raises(SessionNotFoundError):
        resolve_session_prefix("abc123", store, any_workspace=False)

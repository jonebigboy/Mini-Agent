"""Test the in-progress session detection prompt."""
from unittest.mock import patch

import pytest

from mini_agent.session.writer import SessionWriter


def test_inprogress_prompt_offered_when_unended_session_exists(mini_agent_home, tmp_path):
    """If --continue/--resume not given AND an in-progress session exists,
    prompt the user (defaulting to continue)."""
    w = SessionWriter("inprog-001", str(tmp_path), str(tmp_path), "m", {})
    w.open()
    w.append_user_message("mid-conversation")
    # Release lock without writing session_end — simulates crashed process
    w._lock.release()
    w._opened = False
    if w._fh:
        w._fh.flush()
        w._fh.close()
        w._fh = None

    with patch("builtins.input", return_value="1") as mock_input:
        from mini_agent.cli import maybe_prompt_in_progress
        result = maybe_prompt_in_progress(str(tmp_path))
        assert result == "inprog-001"
        mock_input.assert_called_once()


def test_inprogress_returns_none_when_no_sessions(mini_agent_home, tmp_path):
    """No sessions → no prompt, returns None."""
    from mini_agent.cli import maybe_prompt_in_progress
    assert maybe_prompt_in_progress(str(tmp_path)) is None


def test_inprogress_user_chooses_new(mini_agent_home, tmp_path):
    """User typing 'n' returns None (start new session)."""
    w = SessionWriter("inprog-002", str(tmp_path), str(tmp_path), "m", {})
    w.open()
    w.append_user_message("x")
    w._lock.release()
    w._opened = False
    if w._fh:
        w._fh.flush()
        w._fh.close()
        w._fh = None

    with patch("builtins.input", return_value="n"):
        from mini_agent.cli import maybe_prompt_in_progress
        assert maybe_prompt_in_progress(str(tmp_path)) is None

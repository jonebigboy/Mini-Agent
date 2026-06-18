"""Tests for `mini-agent session cat/list/tail/rm` subcommands."""
import subprocess
import sys

import pytest


def _run_cli(*args, cwd=None, timeout=10):
    return subprocess.run(
        [sys.executable, "-m", "mini_agent.cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


def test_session_list_empty(mini_agent_home, tmp_path):
    result = _run_cli("session", "list", cwd=str(tmp_path))
    assert result.returncode == 0


def test_session_cat_pretty_prints(mini_agent_home, tmp_path):
    """After writing a session via Writer, `session cat` should pretty-print it."""
    from mini_agent.session.writer import SessionWriter

    w = SessionWriter("cat-test", str(tmp_path), str(tmp_path), "test-model", {})
    w.open()
    w.append_user_message("hello world")
    w.close()

    result = _run_cli("session", "cat", "cat-test", cwd=str(tmp_path))
    assert "hello world" in result.stdout
    assert "USER" in result.stdout or "user" in result.stdout.lower()


def test_session_cat_json_format(mini_agent_home, tmp_path):
    import json

    from mini_agent.session.writer import SessionWriter

    w = SessionWriter("json-test", str(tmp_path), str(tmp_path), "m", {})
    w.open()
    w.append_user_message("hi")
    w.close()

    result = _run_cli(
        "session", "cat", "json-test", "--format", "json", cwd=str(tmp_path)
    )
    # Output should be parseable JSON lines
    for line in result.stdout.strip().split("\n"):
        if line:
            json.loads(line)  # does not raise


def test_session_rm_deletes(mini_agent_home, tmp_path):
    from mini_agent.session.store import SessionStore
    from mini_agent.session.writer import SessionWriter

    w = SessionWriter("rm-test", str(tmp_path), str(tmp_path), "m", {})
    w.open()
    w.close()

    store = SessionStore(str(tmp_path))
    assert store.session_path("rm-test").exists()

    result = _run_cli("session", "rm", "rm-test", cwd=str(tmp_path))
    assert result.returncode == 0
    assert not store.session_path("rm-test").exists()


def test_session_cat_missing_session_returns_error(mini_agent_home, tmp_path):
    result = _run_cli("session", "cat", "no-such-sid", cwd=str(tmp_path))
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()


def test_session_list_with_sessions(mini_agent_home, tmp_path):
    from mini_agent.session.writer import SessionWriter

    for sid in ("alpha", "beta"):
        w = SessionWriter(sid, str(tmp_path), str(tmp_path), "m", {})
        w.open()
        w.append_user_message(f"msg from {sid}")
        w.close()

    result = _run_cli("session", "list", cwd=str(tmp_path))
    assert result.returncode == 0
    assert "alpha" in result.stdout
    assert "beta" in result.stdout

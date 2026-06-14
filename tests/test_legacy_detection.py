"""Test cases for legacy file detection in cli.py."""

from pathlib import Path
from unittest.mock import patch

from mini_agent.cli import _warn_legacy_files


def test_warn_legacy_files_no_files(tmp_path, capsys):
    """No output when no legacy files exist."""
    _warn_legacy_files(tmp_path)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_warn_legacy_files_global_mini_agent(tmp_path, capsys):
    """Warning printed when ~/.mini-agent/MINI_AGENT.md exists."""
    fake_home = tmp_path / "fake_home"
    (fake_home / ".mini-agent").mkdir(parents=True)
    (fake_home / ".mini-agent" / "MINI_AGENT.md").write_text("# Old config")

    workspace = tmp_path / "workspace"

    with patch.object(Path, "home", return_value=fake_home):
        _warn_legacy_files(workspace)

    captured = capsys.readouterr()
    assert "MINI_AGENT.md" in captured.out
    assert "AGENTS.md" in captured.out


def test_warn_legacy_files_agent_memory_json(tmp_path, capsys):
    """Info printed when .agent_memory.json exists in workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".agent_memory.json").write_text("[]")

    fake_home = tmp_path / "fake_home"

    with patch.object(Path, "home", return_value=fake_home):
        _warn_legacy_files(workspace)

    captured = capsys.readouterr()
    assert ".agent_memory.json" in captured.out
    assert "SessionNoteTool" in captured.out


def test_warn_legacy_files_both_exist(tmp_path, capsys):
    """Both warnings printed when both legacy files exist."""
    fake_home = tmp_path / "fake_home"
    (fake_home / ".mini-agent").mkdir(parents=True)
    (fake_home / ".mini-agent" / "MINI_AGENT.md").write_text("# Old config")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".agent_memory.json").write_text("[]")

    with patch.object(Path, "home", return_value=fake_home):
        _warn_legacy_files(workspace)

    captured = capsys.readouterr()
    assert "MINI_AGENT.md" in captured.out
    assert ".agent_memory.json" in captured.out

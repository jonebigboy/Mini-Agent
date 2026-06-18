"""Tests for paths.py — encoded-cwd and project directory layout."""
from pathlib import Path
from mini_agent.paths import (
    encode_cwd, project_dir, sessions_dir,
    tool_outputs_dir, memory_dir,
)


def test_encode_cwd_basic():
    assert encode_cwd("/Users/jone/foo") == "Users-jone-foo"


def test_encode_cwd_strips_leading_dash():
    assert encode_cwd("/foo/bar").startswith("foo") is True
    assert encode_cwd("/foo/bar") == "foo-bar"


def test_encode_cwd_preserves_underscores():
    """Underscores in path are preserved (not replaced)."""
    assert encode_cwd("/Users/jone/foo_bar") == "Users-jone-foo_bar"


def test_project_dir_uses_encoded_cwd(mini_agent_home):
    p = project_dir("/Users/jone/Mini-Agent")
    assert p == mini_agent_home / ".mini-agent" / "projects" / "Users-jone-Mini-Agent"


def test_sessions_dir(mini_agent_home):
    p = sessions_dir("/Users/jone/Mini-Agent")
    assert p == project_dir("/Users/jone/Mini-Agent") / "sessions"


def test_tool_outputs_dir(mini_agent_home):
    p = tool_outputs_dir("/Users/jone/Mini-Agent", "abc-123")
    assert p == project_dir("/Users/jone/Mini-Agent") / "tool_outputs" / "abc-123"


def test_memory_dir(mini_agent_home):
    p = memory_dir("/Users/jone/Mini-Agent")
    assert p == project_dir("/Users/jone/Mini-Agent") / "memory"

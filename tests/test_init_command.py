"""Tests for the /init command prompt template."""

from mini_agent.commands.init_command import INIT_PROMPT_TEMPLATE


def test_template_has_workspace_placeholder():
    """Template must contain {workspace} so .format() can inject the path."""
    assert "{workspace}" in INIT_PROMPT_TEMPLATE


def test_template_formats_without_error():
    """format() must succeed and embed the workspace path."""
    result = INIT_PROMPT_TEMPLATE.format(workspace="/tmp/fake-project")
    assert "/tmp/fake-project" in result


def test_template_mentions_required_artifacts():
    """Prompt must direct the agent to the right files and output."""
    result = INIT_PROMPT_TEMPLATE.format(workspace="/tmp/x")
    # Output file
    assert "AGENTS.md" in result
    # Suggested scan targets (representative sentinels — not exhaustive)
    assert "README.md" in result
    assert "pyproject.toml" in result
    # Length constraint (Claude Code best practice)
    assert "60-300" in result

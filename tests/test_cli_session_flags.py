"""Smoke tests for --continue / --resume / --fork flags and `session` subcommand."""
import subprocess
import sys

import pytest


def _run_help():
    return subprocess.run(
        [sys.executable, "-m", "mini_agent.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_cli_help_lists_session_flags():
    """--help should mention --continue/--resume/--fork."""
    result = _run_help()
    assert "--continue" in result.stdout
    assert "--resume" in result.stdout
    assert "--fork" in result.stdout


def test_cli_help_lists_session_subcommand():
    result = _run_help()
    assert "session" in result.stdout.lower()


def test_cli_help_lists_any_workspace_flag():
    result = _run_help()
    assert "--any-workspace" in result.stdout


def test_cli_session_subcommand_has_actions():
    """`mini-agent session --help` should list cat/list/tail/rm."""
    result = subprocess.run(
        [sys.executable, "-m", "mini_agent.cli", "session", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = result.stdout.lower()
    for action in ("list", "cat", "tail", "rm"):
        assert action in out, f"missing session action: {action}"

"""Shared pytest fixtures."""
from pathlib import Path
import pytest


@pytest.fixture
def mini_agent_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a temp dir for isolated session tests.

    All tests that touch ~/.mini-agent should use this fixture to avoid
    polluting the developer's real home directory. Also sets the HOME
    environment variable so subprocesses (e.g. `mini-agent session list`
    invoked via subprocess.run) inherit the same fake home.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))  # Windows
    return fake_home

"""Tests for AgentLogger per-session granularity and TTL cleanup."""

from pathlib import Path

from mini_agent.logger import AgentLogger


def test_start_new_session_creates_one_file(tmp_path, monkeypatch):
    """Constructing AgentLogger creates exactly one log file in the session dir."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()

    log_dir = fake_home / ".mini-agent" / "log"
    files = list(log_dir.glob("*.log"))
    assert len(files) == 1, f"Expected 1 file, found {len(files)}"
    assert logger.get_log_file_path() == files[0]


def test_multiple_run_sections_share_file(tmp_path, monkeypatch):
    """Three start_new_run_section calls produce one file with two separators."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()
    file_after_init = logger.get_log_file_path()

    logger.start_new_run_section()  # run #1, no separator
    logger.start_new_run_section()  # run #2, writes separator
    logger.start_new_run_section()  # run #3, writes separator

    log_dir = fake_home / ".mini-agent" / "log"
    files = list(log_dir.glob("*.log"))
    assert len(files) == 1, f"Expected 1 file, found {len(files)}"
    assert logger.get_log_file_path() == file_after_init

    # First run has no separator; second and third each write one separator.
    text = logger.get_log_file_path().read_text(encoding="utf-8")
    separator_count = text.count("--- Run #")
    assert separator_count == 2, f"Expected 2 separators, found {separator_count}"


def test_run_section_separator_format(tmp_path, monkeypatch):
    """Run separator contains run number, ISO-ish timestamp, and 80-char rule."""
    import re

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()
    logger.start_new_run_section()  # run #1, no separator
    logger.start_new_run_section()  # run #2, writes separator

    text = logger.get_log_file_path().read_text(encoding="utf-8")
    pattern = r"--- Run #2 started at \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    assert re.search(pattern, text), f"Separator format mismatch in:\n{text}"

    # Separator block must be wrapped by 80-char '=' rules.
    lines = text.splitlines()
    sep_idx = next(i for i, ln in enumerate(lines) if ln.startswith("--- Run #2"))
    assert lines[sep_idx - 1] == "=" * 80
    assert lines[sep_idx + 1] == "=" * 80


def test_log_index_resets_per_run(tmp_path, monkeypatch):
    """log_index resets to 0 at the start of each run section."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()
    logger.start_new_run_section()  # run #1
    logger.log_request(messages=[], tools=None)  # writes [1]
    logger.log_request(messages=[], tools=None)  # writes [2]

    logger.start_new_run_section()  # run #2
    logger.log_request(messages=[], tools=None)  # should be [1] again

    text = logger.get_log_file_path().read_text(encoding="utf-8")
    # After run #2 separator, the first entry must be [1] not [3].
    sep_pos = text.find("--- Run #2")
    after_sep = text[sep_pos:]
    assert "[1] REQUEST" in after_sep, (
        f"Expected [1] REQUEST after run #2 separator:\n{after_sep}"
    )
    assert "[3] REQUEST" not in text, "log_index did not reset between runs"


def test_cleanup_removes_old_files(tmp_path, monkeypatch):
    """Files older than 30 days are deleted at session start."""
    import os
    import time

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    log_dir = fake_home / ".mini-agent" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create an old log file.
    old_file = log_dir / "agent_run_OLD.log"
    old_file.write_text("old", encoding="utf-8")
    old_mtime = time.time() - (31 * 24 * 3600)  # 31 days ago
    os.utime(old_file, (old_mtime, old_mtime))

    # Constructing a new logger triggers cleanup of the old file.
    AgentLogger()

    assert not old_file.exists(), "Old log file should have been deleted"


def test_cleanup_preserves_recent_files(tmp_path, monkeypatch):
    """Files newer than 30 days are NOT deleted."""
    import os
    import time

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    log_dir = fake_home / ".mini-agent" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    recent_file = log_dir / "agent_run_RECENT.log"
    recent_file.write_text("recent", encoding="utf-8")
    recent_mtime = time.time() - (5 * 24 * 3600)  # 5 days ago
    os.utime(recent_file, (recent_mtime, recent_mtime))

    AgentLogger()

    assert recent_file.exists(), "Recent log file should NOT have been deleted"


def test_cleanup_preserves_just_created_file(tmp_path, monkeypatch):
    """The file created by start_new_session survives cleanup even if
    filesystem mtime is weird."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()
    assert logger.get_log_file_path().exists(), \
        "Session log file must still exist after construction"


def test_cleanup_handles_missing_dir(tmp_path, monkeypatch):
    """_cleanup_old_logs does not crash when log_dir is missing."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()
    # Manually remove the dir and re-invoke cleanup to simulate weird state.
    import shutil
    shutil.rmtree(logger.log_dir)

    # Should return 0 without raising.
    deleted = logger._cleanup_old_logs()
    assert deleted == 0

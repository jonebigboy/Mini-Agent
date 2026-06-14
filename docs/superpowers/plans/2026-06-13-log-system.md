# Log System Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change log granularity from per-run to per-session (one file per CLI session) and add 30-day TTL auto-cleanup at session start.

**Architecture:** `AgentLogger` creates its log file once in `__init__` (via a renamed `start_new_session()`). Each `Agent.run()` call invokes a new `start_new_run_section()` that writes only a separator header (after the first run). Cleanup runs immediately after file creation, scanning `~/.mini-agent/log/*.log` for files with `st_mtime` older than 30 days.

**Tech Stack:** Python 3.10+, pathlib, pytest with `tmp_path` + `monkeypatch`.

**Spec:** `docs/superpowers/specs/2026-06-13-log-system-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `mini_agent/logger.py` | Modify | Add `start_new_session`, `start_new_run_section`, `_cleanup_old_logs`; remove old `start_new_run` |
| `mini_agent/agent.py:84` | Modify | Move log-file print to `__init__` |
| `mini_agent/agent.py:348-349` | Modify | Replace `start_new_run()` with `start_new_run_section()`; drop the print |
| `tests/test_logger.py` | Create | All unit tests for new logger behavior |

No new modules. `cli.py` is untouched (existing `mini-agent log` and `/log` commands work unchanged because they glob `*.log` by filename).

---

## Task 1: Set up the test file and run the baseline

**Files:**
- Create: `tests/test_logger.py`

- [ ] **Step 1: Create empty test file with module docstring**

Create `tests/test_logger.py` with just:

```python
"""Tests for AgentLogger per-session granularity and TTL cleanup."""
```

- [ ] **Step 2: Run pytest to confirm collection works**

Run: `pytest tests/test_logger.py -v`
Expected: `collected 0 items` / `no tests ran` (no failure). This confirms the file is discovered.

- [ ] **Step 3: Commit**

```bash
git add tests/test_logger.py
git commit -m "test: scaffold logger test module"
```

---

## Task 2: Test that `start_new_session` creates exactly one file

**Files:**
- Test: `tests/test_logger.py`
- Modify (later): `mini_agent/logger.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_logger.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_logger.py::test_start_new_session_creates_one_file -v`
Expected: FAIL — current `AgentLogger.__init__` does not create a file (only `start_new_run` does, and we haven't called it).

- [ ] **Step 3: Implement `start_new_session` and call it from `__init__`**

In `mini_agent/logger.py`:

1. Add `run_count` attribute. Replace the existing `__init__` body:

```python
def __init__(self):
    """Initialize logger.

    Logs are stored in ~/.mini-agent/log/ directory. A new log file is
    created immediately for the session (one file per CLI session).
    """
    self.log_dir = Path.home() / ".mini-agent" / "log"
    self.log_dir.mkdir(parents=True, exist_ok=True)
    self.log_file = None
    self.log_index = 0
    self.run_count = 0
    self.start_new_session()
```

2. Rename the existing `start_new_run` to `start_new_session`. Update its docstring and set `run_count = 0` (instead of leaving it unset):

```python
def start_new_session(self):
    """Start a new session: create a new log file and reset counters.

    Called automatically from __init__. One AgentLogger instance maps to
    one CLI session, which maps to one log file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"agent_run_{timestamp}.log"
    self.log_file = self.log_dir / log_filename
    self.log_index = 0
    self.run_count = 0

    # Write log header
    with open(self.log_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"Agent Run Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")
```

(Note: keep the file header text identical to the current behavior — only the method name and counter reset change.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_start_new_session_creates_one_file -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mini_agent/logger.py tests/test_logger.py
git commit -m "feat(logger): create log file at construction (per-session granularity)"
```

---

## Task 3: Test that multiple `start_new_run_section` calls share one file

**Files:**
- Test: `tests/test_logger.py`
- Modify: `mini_agent/logger.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_logger.py`:

```python
def test_multiple_run_sections_share_file(tmp_path, monkeypatch):
    """Three start_new_run_section calls produce one file with two separators."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()
    file_after_init = logger.get_log_file_path()

    logger.start_new_run_section()
    logger.start_new_run_section()

    log_dir = fake_home / ".mini-agent" / "log"
    files = list(log_dir.glob("*.log"))
    assert len(files) == 1, f"Expected 1 file, found {len(files)}"
    assert logger.get_log_file_path() == file_after_init

    # First run has no separator; second and third each write one separator.
    text = logger.get_log_file_path().read_text(encoding="utf-8")
    separator_count = text.count("--- Run #")
    assert separator_count == 2, f"Expected 2 separators, found {separator_count}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_logger.py::test_multiple_run_sections_share_file -v`
Expected: FAIL — `start_new_run_section` does not exist (`AttributeError`).

- [ ] **Step 3: Implement `start_new_run_section`**

In `mini_agent/logger.py`, add this method (place it immediately after `start_new_session`):

```python
def start_new_run_section(self):
    """Mark the start of a new run within the current session.

    Writes a separator header before each run except the first (which is
    already covered by the session file header). Resets log_index so the
    [N] entry numbers are scoped per-run.
    """
    self.run_count += 1
    self.log_index = 0

    if self.run_count == 1:
        # First run: file header from start_new_session already covers it.
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"--- Run #{self.run_count} started at {timestamp} "
    num_dashes = max(0, 80 - len(prefix))
    with open(self.log_file, "a", encoding="utf-8") as f:
        f.write("\n" + "=" * 80 + "\n")
        f.write(prefix + "-" * num_dashes + "\n")
        f.write("=" * 80 + "\n")
```

Note: the separator text contains the literal substring `--- Run #` which the test counts. The full separator line is padded with dashes to 80 chars to match the `=` rule width above and below it.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_multiple_run_sections_share_file -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mini_agent/logger.py tests/test_logger.py
git commit -m "feat(logger): add start_new_run_section for per-run separators"
```

---

## Task 4: Test the run section separator format

**Files:**
- Test: `tests/test_logger.py`

This task pins the exact separator text so future changes don't silently break consumers that grep logs.

- [ ] **Step 1: Write the test**

Append to `tests/test_logger.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_run_section_separator_format -v`
Expected: PASS (implementation from Task 3 already produces this format).

- [ ] **Step 3: Commit**

```bash
git add tests/test_logger.py
git commit -m "test(logger): pin run separator format"
```

---

## Task 5: Test that `log_index` resets per run

**Files:**
- Test: `tests/test_logger.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_logger.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_log_index_resets_per_run -v`
Expected: PASS (Task 3 implementation already resets `log_index = 0`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_logger.py
git commit -m "test(logger): verify log_index resets per run section"
```

---

## Task 6: Test that TTL cleanup removes old files

**Files:**
- Test: `tests/test_logger.py`
- Modify: `mini_agent/logger.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_logger.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_logger.py::test_cleanup_removes_old_files -v`
Expected: FAIL — no cleanup exists yet, old file still present.

- [ ] **Step 3: Implement `_cleanup_old_logs` and call it from `start_new_session`**

In `mini_agent/logger.py`:

1. Add a module-level constant at the top of the file (after the imports):

```python
LOG_TTL_DAYS = 30
```

2. Add the cleanup method to `AgentLogger` (place after `start_new_session`):

```python
def _cleanup_old_logs(self, max_age_days: int = LOG_TTL_DAYS) -> int:
    """Delete log files older than max_age_days.

    Returns the number of files deleted. Errors on individual files are
    swallowed (printed as warnings) so a single bad file cannot abort
    session startup. The file just created by start_new_session is never
    deleted because its mtime is current.
    """
    now = datetime.now().timestamp()
    cutoff = now - (max_age_days * 24 * 3600)
    deleted = 0

    for entry in self.log_dir.glob("*.log"):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                entry.unlink()
                deleted += 1
            except OSError as exc:
                # Don't abort startup over one unremovable file.
                print(f"[logger] could not delete {entry}: {exc}")

    return deleted
```

3. Call cleanup from `start_new_session`, immediately AFTER the file header is written (so the just-created file's mtime is current and cannot match the cutoff). Update `start_new_session` to end with:

```python
    # Write log header
    with open(self.log_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"Agent Run Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")

    # Prune logs older than the TTL.
    removed = self._cleanup_old_logs()
    if removed:
        print(f"🗑 Cleaned {removed} log file(s) older than {LOG_TTL_DAYS} days")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_cleanup_removes_old_files -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mini_agent/logger.py tests/test_logger.py
git commit -m "feat(logger): TTL cleanup of logs older than 30 days at session start"
```

---

## Task 7: Test that recent files survive cleanup

**Files:**
- Test: `tests/test_logger.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_logger.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_cleanup_preserves_recent_files -v`
Expected: PASS (Task 6 implementation already handles this via cutoff comparison).

- [ ] **Step 3: Commit**

```bash
git add tests/test_logger.py
git commit -m "test(logger): confirm recent logs survive TTL cleanup"
```

---

## Task 8: Test that the just-created session file is never deleted

**Files:**
- Test: `tests/test_logger.py`

Defensive test for the edge case called out in the spec.

- [ ] **Step 1: Write the test**

Append to `tests/test_logger.py`:

```python
def test_cleanup_preserves_just_created_file(tmp_path, monkeypatch):
    """The file created by start_new_session survives cleanup even if
    filesystem mtime is weird."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    logger = AgentLogger()
    assert logger.get_log_file_path().exists(), \
        "Session log file must still exist after construction"
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_cleanup_preserves_just_created_file -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_logger.py
git commit -m "test(logger): confirm session file survives cleanup edge case"
```

---

## Task 9: Test cleanup handles a missing log directory gracefully

**Files:**
- Test: `tests/test_logger.py`

Although `__init__` creates the directory before calling `start_new_session`, `_cleanup_old_logs` should be defensive: globbing a missing dir returns no files (no crash).

- [ ] **Step 1: Write the test**

Append to `tests/test_logger.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_logger.py::test_cleanup_handles_missing_dir -v`
Expected: PASS — `Path.glob` on a missing directory yields nothing (no exception).

- [ ] **Step 3: Commit**

```bash
git add tests/test_logger.py
git commit -m "test(logger): verify cleanup tolerates missing log directory"
```

---

## Task 10: Wire `agent.py` to the new logger API

**Files:**
- Modify: `mini_agent/agent.py:84` (after `self.logger = AgentLogger()`)
- Modify: `mini_agent/agent.py:348-349`

- [ ] **Step 1: Move the log-file print to `__init__`**

In `mini_agent/agent.py`, find this block at line ~84 (inside `Agent.__init__`):

```python
        # Initialize logger
        self.logger = AgentLogger()
```

Replace with:

```python
        # Initialize logger (creates one log file for the whole session)
        self.logger = AgentLogger()
        print(f"{Colors.DIM}📝 Log file: {self.logger.get_log_file_path()}{Colors.RESET}")
```

- [ ] **Step 2: Replace `start_new_run()` call in `Agent.run()`**

In `mini_agent/agent.py`, find this block at line ~347-349 (inside `Agent.run()`):

```python
        # Start new run, initialize log file
        self.logger.start_new_run()
        print(f"{Colors.DIM}📝 Log file: {self.logger.get_log_file_path()}{Colors.RESET}")
```

Replace with:

```python
        # Start a new run section within the session log file
        self.logger.start_new_run_section()
```

The print is removed here (moved to `__init__` in Step 1).

- [ ] **Step 3: Verify no remaining references to the old method**

Run: `grep -rn "start_new_run\b" mini_agent/`
Expected: no matches. (`start_new_run_section` matches are fine; only bare `start_new_run` is forbidden.)

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all tests pass, including the new `tests/test_logger.py` tests and the existing suite.

- [ ] **Step 5: Commit**

```bash
git add mini_agent/agent.py
git commit -m "feat(agent): use per-session log file and per-run sections"
```

---

## Task 11: Manual integration verification

**Files:** none (verification only)

- [ ] **Step 1: Start the agent in interactive mode**

Run: `uv run python -m mini_agent.cli`

Expected output includes:
- `📝 Log file: /Users/<you>/.mini-agent/log/agent_run_YYYYMMDD_HHMMSS.log` printed ONCE at startup
- (If old logs exist) `🗑 Cleaned N log file(s) older than 30 days` printed once before the banner

- [ ] **Step 2: Issue two short tasks in the same session**

Type any two short prompts (e.g. `say hi`, then `say bye`). Wait for each to complete.

- [ ] **Step 3: Confirm only one new log file was created**

Run: `ls -lt ~/.mini-agent/log/*.log | head -3`
Expected: only ONE file with today's timestamp at the top. Its size should grow between runs but the file count for this session is 1.

- [ ] **Step 4: Confirm the file contains a run separator**

Open the latest log file. Expected structure:

```
================================================================================
Agent Run Log - 2026-06-13 14:23:01
================================================================================

[1] REQUEST
... (run #1 entries)

================================================================================
--- Run #2 started at 2026-06-13 14:23:15 --------------------------------------
================================================================================

[1] REQUEST
... (run #2 entries)
```

- [ ] **Step 5: Confirm `📝 Log file:` did NOT print twice**

Scroll back through the session output. The message should appear once, at startup, not before each run.

No commit for this task — verification only. If anything fails, file an issue against the relevant task above and fix before merging.

---

## Spec Coverage Checklist

Run through this when the plan is complete to confirm nothing was missed:

- [x] Per-session granularity → Tasks 2, 3
- [x] Run separator format (Run #N, timestamp, 80-char rule) → Tasks 3, 4
- [x] log_index resets per run → Task 5
- [x] 30-day TTL cleanup at session start → Task 6
- [x] Cleanup preserves recent files → Task 7
- [x] Cleanup preserves the just-created session file → Task 8
- [x] Cleanup tolerates missing directory → Task 9
- [x] `agent.py` wiring (print move + `start_new_run_section` call) → Task 10
- [x] Manual integration check (one file per session, separator visible, print appears once) → Task 11
- [x] No changes to `cli.py` (existing log commands unchanged) → noted in File Structure
- [x] Backward-compatible filename (`agent_run_*.log`) → preserved in Task 2

## Out of Scope (deferred)

Per the spec: Memory system changes are explicitly deferred. `note_tool.py`, `memory_manager.py`, and `system_prompt.md` are NOT touched by this plan.

# Log System Redesign

**Date:** 2026-06-13
**Status:** Approved (in implementation phase)
**Scope:** Log file granularity and cleanup only. Memory system deliberately deferred.

## Problem Statement

The current log system has two issues:

1. **Per-run granularity produces excessive file fragmentation.**
   `AgentLogger.start_new_run()` is called at the top of every `Agent.run()` invocation
   (`agent.py:348`). Each call creates a brand new `agent_run_YYYYMMDD_HHMMSS.log` file.
   In practice this means a single CLI session that issues multiple `/run` commands
   (or short-lived runs) generates one file per run. The `~/.mini-agent/log/` directory
   has accumulated **110 files** in roughly three months, several of them written only
   seconds apart.

2. **No cleanup mechanism.** Logs accumulate indefinitely. Nothing prunes old files,
   so the directory grows without bound.

## Industry Baseline

Researched approaches used by comparable tools:

| Tool | Granularity | Cleanup |
|------|-------------|---------|
| Claude Code | One JSONL file per session | 30-day TTL auto-delete |
| Codex CLI | One JSONL file per session | None built-in |
| Aider | Single growing `.md` per project | None built-in |
| Gemini CLI | Manual `/chat save` | None |

The dominant pattern is **one file per session** (not per run), with **time-based TTL**
as the most common cleanup strategy. This spec adopts both conventions.

## Goals

1. One log file per CLI session. Multiple `agent.run()` invocations within the same
   session append to the same file, separated by a clearly marked run header.
2. Automatic cleanup of log files older than 30 days, triggered at session start.
3. Preserve backward compatibility: existing `mini-agent log` and `/log` commands
   that read files by name continue to work unchanged.

## Non-Goals

- Memory system changes (deferred — separate spec pending).
- Log format changes (JSON-in-text layout stays as-is).
- Configurable retention period (30-day TTL is hardcoded for now; can be exposed
  via `config.yaml` later if needed).
- Per-project log isolation (all sessions still write to `~/.mini-agent/log/`).

## Design

### Granularity: Per-Session Files

**Current flow:**

```
Agent.__init__  →  AgentLogger()  (no file created yet)
Agent.run()     →  self.logger.start_new_run()  →  creates new file every run
```

**New flow:**

```
Agent.__init__  →  AgentLogger()  →  start_new_session()  →  creates file ONCE
Agent.run()     →  self.logger.start_new_run_section()  →  writes separator only
```

**File naming:** unchanged — `agent_run_YYYYMMDD_HHMMSS.log`, where the timestamp
is the **session start time**. Keeping the prefix avoids breaking existing log
readers; the semantic shift from "run time" to "session time" is invisible to
consumers that only read filenames.

**Run separator format** (written at the top of each `agent.run()` invocation
starting from the second run in a session — the first run is already covered
by the file header from `start_new_session()`):

```
================================================================================
--- Run #2 started at 2026-06-13 14:23:01 --------------------------------------
================================================================================
```

The run counter is per-session (resets to 1 when a new session starts).

### TTL Cleanup

Triggered once at session start, immediately after `start_new_session()` creates
the new log file.

**Algorithm:**

1. Scan `~/.mini-agent/log/` for `*.log` files.
2. For each file, compare `st_mtime` to `now`.
3. Delete files older than 30 days.
4. Print a one-line summary to the console:
   - `🗑 Cleaned 12 log files older than 30 days` (only if any were deleted)
   - Nothing printed if zero files were deleted (avoid noise on normal sessions)

**Why startup-time cleanup (not background thread):**
- Simpler, no threading concerns.
- Predictable: user sees cleanup output before the session starts.
- Cost is negligible (110 files, single `stat` + `unlink` per file).
- Matches the natural "session boundary" mental model.

**Edge cases:**
- Cleanup never deletes the file just created by `start_new_session()`.
- Permission errors on individual files are logged to console as warnings but
  do not abort the session.
- If `~/.mini-agent/log/` doesn't exist (fresh install), `mkdir(parents=True)`
  runs first; cleanup becomes a no-op.

### Code Changes

**`mini_agent/logger.py`** (primary changes):

- Rename `start_new_run()` → `start_new_session()`. Behavior: create the file,
  write the file header (same as current), reset `log_index` to 0, set
  `run_count = 0`.
- Add `start_new_run_section()`. Behavior:
  1. Increment `run_count` by 1 (so it becomes 1 on the first call).
  2. If `run_count > 1`, write the run separator header. (First run has no
     separator — the file header from `start_new_session()` already marks it.)
  3. Reset `log_index` to 0 so `[N]` indices are scoped per-run.
- Add `_cleanup_old_logs(max_age_days: int = 30)`. Called from
  `start_new_session()` after the new file is created.
- Add `run_count: int` instance attribute alongside the existing `log_index`.

**`mini_agent/agent.py:348-349`** (top of `Agent.run()`):

```python
# Before
self.logger.start_new_run()
print(f"{Colors.DIM}📝 Log file: {self.logger.get_log_file_path()}{Colors.RESET}")

# After
self.logger.start_new_run_section()
```

The print is removed here (moved to `__init__` per the note above).

**`mini_agent/agent.py:84`** (`self.logger = AgentLogger()`):

The `AgentLogger.__init__` constructor calls `start_new_session()` internally
so that the log file exists as soon as the Agent is constructed. This matches
the existing lifecycle: one Agent per CLI session, one log file per Agent.

**The `📝 Log file: ...` print moves from `agent.py:349` (inside `Agent.run()`)
to `agent.py` `__init__`, immediately after `self.logger = AgentLogger()`.**
This way the message prints once at session start, not once per run. Keeping
the print in `agent.py` (rather than inside `logger.py`) avoids pulling the
`Colors` class into the logger module.

**No changes to `cli.py`** — the existing `mini-agent log` and `/log` commands
read files by globbing `*.log`, which still works. They never depended on the
per-run creation semantics.

### What Stays the Same

- Log directory: `~/.mini-agent/log/`
- Log format: numbered entries (`[N] REQUEST`, `[N] RESPONSE`, `[N] TOOL_RESULT`)
  with timestamp + JSON body
- File extension: `.log`
- The `/log` interactive command and `mini-agent log <filename>` CLI command

## Testing

**Unit tests** (new file `tests/test_logger.py`):

1. `test_start_new_session_creates_one_file` — Construct `AgentLogger`, assert
   exactly one file exists with the expected header.
2. `test_multiple_run_sections_share_file` — Call `start_new_run_section()`
   three times, assert only one file exists and it contains three run sections
   (well, two separators — first run has no separator).
3. `test_run_section_separator_format` — Assert the separator text matches the
   spec exactly (run number, timestamp format, separator line width).
4. `test_log_index_resets_per_run` — Log a request in run 1 (index 1), start
   new run section, log another request (index should be 1 again, not 2).
5. `test_cleanup_removes_old_files` — Create fake log files with mtime >30 days
   ago via `os.utime`, call `_cleanup_old_logs`, assert they were deleted.
6. `test_cleanup_preserves_recent_files` — Same setup but mtime within 30 days,
   assert files survive.
7. `test_cleanup_preserves_just_created_file` — Even if the freshly created
   session file somehow has an old mtime, it must not be deleted.
8. `test_cleanup_handles_missing_dir` — Point `log_dir` at a non-existent path,
   call cleanup, assert no crash.

Tests will use `tmp_path` fixture and monkey-patch `Path.home()` to redirect
`~/.mini-agent/log/` to a temp directory.

**Integration check** (manual, not automated):

1. Run `mini-agent` interactively.
2. Issue two `/run` commands.
3. Confirm only ONE new `.log` file was created.
4. Confirm the file contains two run sections with separators.
5. Confirm `📝 Log file: ...` printed only once.

## Rollout

No migration step needed. Existing 110 log files in `~/.mini-agent/log/` will
be gradually pruned by the 30-day TTL over the next month. Nothing forces
immediate cleanup of currently-recent files.

The one test note in `.agent_memory.json` is unrelated to this spec (memory
system is deferred) and is left untouched.

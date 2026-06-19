# Memory System Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align Mini-Agent's memory system with industry patterns (Claude Code) by removing the redundant JSON-based SessionNoteTool, disabling the unused MCP knowledge graph, renaming `MINI_AGENT.md` to the industry-standard `AGENTS.md`, and strengthening the system prompt with explicit memory rules.

**Architecture:** Three-tier Markdown memory model unchanged (global rules / project rules / per-project LLM memory). Only the redundant paths are removed and file names standardized. No new code logic except minor legacy-file detection warnings.

**Tech Stack:** Python 3.12+, pytest, Pydantic, PyYAML. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-14-memory-system-cleanup-design.md`

---

## File Structure

**Delete:**
- `mini_agent/tools/note_tool.py` — SessionNoteTool / RecallNoteTool implementation
- `tests/test_note_tool.py` — tests for deleted code

**Modify (code):**
- `mini_agent/tools/__init__.py` — remove note_tool exports
- `mini_agent/cli.py` — remove import, remove `enable_note` branch, add legacy detection
- `mini_agent/config.py` — remove `enable_note` field and parsing
- `mini_agent/tools/memory_manager.py` — rename `MINI_AGENT.md` → `AGENTS.md` constants
- `mini_agent/config/system_prompt.md` — replace Memory System section
- `mini_agent/config/mcp.json` — disable memory server
- `mini_agent/config/config-example.yaml` — remove `enable_note` line

**Modify (tests):**
- `tests/test_memory.py` — `MINI_AGENT.md` → `AGENTS.md`
- `tests/test_session_integration.py` — remove SessionNoteTool usage
- `tests/test_integration.py` — remove SessionNoteTool usage

**Modify (examples):**
- `examples/03_session_notes.py` — add deprecation header
- `examples/04_full_agent.py` — remove SessionNoteTool refs

**Modify (docs):**
- `CLAUDE.md`
- `docs/memory-system-design.md`
- `docs/PRODUCTION_GUIDE.md`, `docs/PRODUCTION_GUIDE_CN.md`
- `docs/run_agent_analysis.md`
- `ARCHITECTURE_ANALYSIS.md`

---

## Task 1: Delete SessionNoteTool source and remove imports

**Files:**
- Delete: `mini_agent/tools/note_tool.py`
- Modify: `mini_agent/tools/__init__.py`
- Modify: `mini_agent/cli.py:39`

- [ ] **Step 1: Delete the note_tool.py file**

```bash
rm mini_agent/tools/note_tool.py
```

- [ ] **Step 2: Update `mini_agent/tools/__init__.py`**

Remove the note_tool import (line 6) and the two `__all__` entries (lines 15-16). Final content:

```python
"""Tools module."""

from .base import Tool, ToolResult
from .bash_tool import BashTool
from .file_tools import EditTool, ReadTool, WriteTool

__all__ = [
    "Tool",
    "ToolResult",
    "ReadTool",
    "WriteTool",
    "EditTool",
    "BashTool",
]
```

- [ ] **Step 3: Remove import from `mini_agent/cli.py`**

Delete line 39:

```python
from mini_agent.tools.note_tool import SessionNoteTool
```

Leave line 38 (`from mini_agent.tools.memory_manager import MemoryManager`) and line 40 (`from mini_agent.tools.skill_tool import create_skill_tools`) intact.

- [ ] **Step 4: Verify import chain is not broken (yet)**

Run:
```bash
python -c "from mini_agent.tools import Tool, ToolResult, ReadTool, WriteTool, EditTool, BashTool; print('OK')"
```
Expected output: `OK`

Note: `from mini_agent.cli import ...` will fail until Task 2 removes the `enable_note` branch. That's expected.

- [ ] **Step 5: Commit**

```bash
git add mini_agent/tools/note_tool.py mini_agent/tools/__init__.py mini_agent/cli.py
git commit -m "refactor: remove SessionNoteTool source code and imports"
```

---

## Task 2: Remove `enable_note` config

**Files:**
- Modify: `mini_agent/cli.py:516-519`
- Modify: `mini_agent/config.py:54,167`
- Modify: `mini_agent/config/config-example.yaml:46`

- [ ] **Step 1: Remove `enable_note` branch from `mini_agent/cli.py`**

Delete lines 516-519 (the comment + `if` block):

```python
    # Session note tool - needs workspace to store memory file
    if config.tools.enable_note:
        tools.append(SessionNoteTool(memory_file=str(workspace_dir / ".agent_memory.json")))
        print(f"{Colors.GREEN}✅ Loaded session note tool{Colors.RESET}")
```

The preceding block ends at line 514 (`print(f"{Colors.GREEN}✅ Loaded file operation tools...")`) and line 515 is blank. Leave one blank line before the next function.

- [ ] **Step 2: Remove `enable_note` field from `mini_agent/config.py`**

Delete line 54:

```python
    enable_note: bool = True
```

And delete line 167 (inside `from_yaml`):

```python
            enable_note=tools_data.get("enable_note", True),
```

- [ ] **Step 3: Remove `enable_note` from `mini_agent/config/config-example.yaml`**

Delete line 46:

```yaml
  enable_note: true        # Session note tool (SessionNoteTool)
```

- [ ] **Step 4: Verify cli imports cleanly**

Run:
```bash
python -c "from mini_agent.cli import main; print('OK')"
```
Expected output: `OK`

- [ ] **Step 5: Commit**

```bash
git add mini_agent/cli.py mini_agent/config.py mini_agent/config/config-example.yaml
git commit -m "refactor: remove enable_note config and SessionNoteTool loading"
```

---

## Task 3: Update tests that reference SessionNoteTool

**Files:**
- Delete: `tests/test_note_tool.py`
- Modify: `tests/test_session_integration.py:16,40,125-141`
- Modify: `tests/test_integration.py:15,64-71,162-168`

- [ ] **Step 1: Delete `tests/test_note_tool.py`**

```bash
rm tests/test_note_tool.py
```

- [ ] **Step 2: Update `tests/test_session_integration.py`**

Remove the import on line 16:

```python
from mini_agent.tools.note_tool import RecallNoteTool, SessionNoteTool
```

Remove `SessionNoteTool(),` from the tools list (line 40). The list becomes:

```python
    tools = [
        ReadTool(workspace_dir=temp_workspace),
        WriteTool(workspace_dir=temp_workspace),
    ]
```

Delete the entire `test_session_note_persistence` function (lines 125-141), including the `@pytest.mark.asyncio` decorator on line 125.

- [ ] **Step 3: Update `tests/test_integration.py`**

Remove the import on line 15:

```python
from mini_agent.tools.note_tool import RecallNoteTool, SessionNoteTool
```

Remove the SessionNoteTool block in `test_basic_agent_usage` (lines 64-71):

```python
        # Add Note tools for session memory
        memory_file = Path(workspace_dir) / ".agent_memory.json"
        tools.extend(
            [
                SessionNoteTool(memory_file=str(memory_file)),
                RecallNoteTool(memory_file=str(memory_file)),
            ]
        )
```

Remove the SessionNoteTool block in the second test (lines 160-168):

```python
        # Memory file path
        memory_file = Path(workspace_dir) / ".agent_memory.json"

        # Initialize tools (only Session Note Tools for this test)
        tools = [
            SessionNoteTool(memory_file=str(memory_file)),
            RecallNoteTool(memory_file=str(memory_file)),
        ]
```

If this second test loses its primary purpose (it was about session notes), check whether the rest of the test still makes sense. If the test function becomes empty or trivial, delete the entire test function.

- [ ] **Step 4: Run the full test suite**

Run:
```bash
uv run pytest tests/ -v --tb=short 2>&1 | tail -50
```

Expected: All remaining tests pass. No collection errors. No `ImportError` mentioning `note_tool`.

If any test still references SessionNoteTool, the error will name the file — fix it.

- [ ] **Step 5: Commit**

```bash
git add tests/test_note_tool.py tests/test_session_integration.py tests/test_integration.py
git commit -m "test: remove SessionNoteTool references from test suite"
```

---

## Task 4: Rename `MINI_AGENT.md` → `AGENTS.md` in MemoryManager

**Files:**
- Modify: `mini_agent/tools/memory_manager.py:27-28`
- Modify: `tests/test_memory.py` (multiple lines)

- [ ] **Step 1: Update constants in `mini_agent/tools/memory_manager.py`**

Change line 27:

```python
    PROJECT_INSTRUCTION_FILENAME = "MINI_AGENT.md"
```
to:
```python
    PROJECT_INSTRUCTION_FILENAME = "AGENTS.md"
```

Change line 28:

```python
    GLOBAL_INSTRUCTION_FILE = Path.home() / ".mini-agent" / "MINI_AGENT.md"
```
to:
```python
    GLOBAL_INSTRUCTION_FILE = Path.home() / ".mini-agent" / "AGENTS.md"
```

- [ ] **Step 2: Update `tests/test_memory.py`**

Replace every `MINI_AGENT.md` with `AGENTS.md`. The references are at lines: 91, 99, 104, 119, 120, 125, 151, 219, 221, 235, 237, 239.

Run to verify count:
```bash
grep -c "MINI_AGENT.md" tests/test_memory.py
```
Expected: `11` (or however many — replace all).

Use the editor's find-and-replace: `MINI_AGENT.md` → `AGENTS.md`. Also update docstrings that mention `MINI_AGENT.md` (e.g. line 91 `"Test loading when MINI_AGENT.md doesn't exist."` → `"Test loading when AGENTS.md doesn't exist."`).

- [ ] **Step 3: Run memory tests**

Run:
```bash
uv run pytest tests/test_memory.py -v
```
Expected: All 19 tests pass.

- [ ] **Step 4: Check for project-root MINI_AGENT.md (rename if exists)**

Run:
```bash
ls MINI_AGENT.md 2>/dev/null && echo "EXISTS" || echo "NOT FOUND"
```

If output is `EXISTS`:
```bash
git mv MINI_AGENT.md AGENTS.md
```

If output is `NOT FOUND`: no action needed (confirmed absent at plan-writing time).

- [ ] **Step 5: Commit**

```bash
git add mini_agent/tools/memory_manager.py tests/test_memory.py
git commit -m "refactor: rename MINI_AGENT.md to AGENTS.md (industry standard)"
```

If Step 4 renamed a file, also `git add AGENTS.md` before committing.

---

## Task 5: Replace system_prompt.md Memory System section

**Files:**
- Modify: `mini_agent/config/system_prompt.md:74-79`

- [ ] **Step 1: Replace the Memory System section**

The current section (lines 74-79) reads:

```markdown
### Memory System
- Persistent cross-session memory stored as Markdown files in the memory directory
- **MEMORY.md** (index, first 200 lines) and **MINI_AGENT.md** (project instructions) are auto-loaded at session start
- Use **Read/Write/Edit** tools to manage memory files
- Proactively save important findings, patterns, decisions, and user preferences to memory
- Organize by topic into separate files (e.g., `patterns.md`, `debugging.md`), keep MEMORY.md as a concise index
```

Replace with:

```markdown
### Memory System

Cross-session memory is auto-loaded into your context. Manage it with standard file tools
(read_file / write_file / edit_file) — no dedicated memory tools.

#### Rules
- `MEMORY.md`: **max 150 lines**. Topic files (`*.md`): **max 100 lines** each.
- Before writing: **check for duplicates**; near limits, **consolidate** (merge or demote to topic files).
- Write only durable facts: user preferences, confirmed conventions, decisions+rationale, gotchas.
- Skip: ephemeral task context, info already in AGENTS.md/code, speculation, duplicates.
- When unsure, don't write — noisy memory is worse than no memory.
```

- [ ] **Step 2: Verify file is valid Markdown**

Run:
```bash
head -90 mini_agent/config/system_prompt.md
```
Visually confirm the new section sits between "Best Practices" and "Workspace Context".

- [ ] **Step 3: Commit**

```bash
git add mini_agent/config/system_prompt.md
git commit -m "docs: strengthen Memory System prompt with hard size limits and dedup rules"
```

---

## Task 6: Disable MCP memory server

**Files:**
- Modify: `mini_agent/config/mcp.json:26`

- [ ] **Step 1: Change `disabled` to `true`**

In `mini_agent/config/mcp.json`, line 26, change:

```json
            "disabled": false
```
to:
```json
            "disabled": true
```

The full `memory` block should read:

```json
        "memory": {
            "description": "Memory - Knowledge graph memory system (long-term memory based on graph database)",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-memory"
            ],
            "disabled": true
        }
```

- [ ] **Step 2: Verify JSON is valid**

Run:
```bash
python -c "import json; json.load(open('mini_agent/config/mcp.json')); print('valid')"
```
Expected: `valid`

- [ ] **Step 3: Commit**

```bash
git add mini_agent/config/mcp.json
git commit -m "config: disable MCP knowledge graph memory server"
```

---

## Task 7: Update examples

**Files:**
- Modify: `examples/03_session_notes.py` (add header)
- Modify: `examples/04_full_agent.py:15,81-89` (remove refs)

- [ ] **Step 1: Add deprecation header to `examples/03_session_notes.py`**

Insert at the very top of the file (before any existing import or docstring), a comment block:

```python
# DEPRECATED: SessionNoteTool has been removed from Mini-Agent core.
# This example is preserved as a conceptual reference for pluggable
# storage backend patterns. The current memory system uses
# MemoryManager + Markdown files. See docs/memory-system-design.md.
```

Do not modify the rest of the file. It will not run (imports fail), which is the accepted trade-off.

- [ ] **Step 2: Remove SessionNoteTool from `examples/04_full_agent.py`**

Remove the import on line 21 (or wherever `from mini_agent.tools.note_tool import ...` appears):

```python
from mini_agent.tools.note_tool import RecallNoteTool, SessionNoteTool
```

Remove the Session Note tools block (lines 81-89):

```python
        # Add Session Note tools
        memory_file = Path(workspace_dir) / ".agent_memory.json"
        tools.extend(
            [
                SessionNoteTool(memory_file=str(memory_file)),
                RecallNoteTool(memory_file=str(memory_file)),
            ]
        )
        print("✓ Loaded 2 Session Note tools")
```

Replace with a single comment:

```python
        # SessionNoteTool removed — use MemoryManager + AGENTS.md instead.
```

- [ ] **Step 3: Verify 04_full_agent.py imports cleanly**

Run:
```bash
python -c "import ast; ast.parse(open('examples/04_full_agent.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 4: Commit**

```bash
git add examples/03_session_notes.py examples/04_full_agent.py
git commit -m "docs: deprecate session_notes example, update full_agent example"
```

---

## Task 8: Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/memory-system-design.md`
- Modify: `docs/PRODUCTION_GUIDE.md`, `docs/PRODUCTION_GUIDE_CN.md`
- Modify: `docs/run_agent_analysis.md`
- Modify: `ARCHITECTURE_ANALYSIS.md`

- [ ] **Step 1: Find all references**

Run:
```bash
grep -rn "SessionNoteTool\|RecallNoteTool\|enable_note\|MINI_AGENT\.md" CLAUDE.md docs/memory-system-design.md docs/PRODUCTION_GUIDE.md docs/PRODUCTION_GUIDE_CN.md docs/run_agent_analysis.md ARCHITECTURE_ANALYSIS.md
```

Note every line output — each needs updating.

- [ ] **Step 2: Update `CLAUDE.md`**

- Remove the line under "### 核心组件" mentioning `note_tool.py` — `SessionNoteTool` / the note_tool reference.
- In the "### MCP 集成" section, the line about preconfigured servers — update to note `memory` is disabled.
- Replace every `MINI_AGENT.md` with `AGENTS.md`.

- [ ] **Step 3: Update `docs/memory-system-design.md`**

This is the biggest doc change. Key edits:

1. Replace every `MINI_AGENT.md` → `AGENTS.md` (file paths, table rows, examples).
2. Replace `~/.mini-agent/MINI_AGENT.md` → `~/.mini-agent/AGENTS.md` in the three-tier table and all path references.
3. Replace `<项目根目录>/MINI_AGENT.md` → `<项目根目录>/AGENTS.md`.
4. In the "兼容性" section near the bottom, remove the lines about coexistence with SessionNoteTool — replace with a note that SessionNoteTool has been removed.
5. In the industry comparison table, update the Mini-Agent column: `MINI_AGENT.md` → `AGENTS.md`.
6. Update "## 文件变更清单" if it references the old filename.

- [ ] **Step 4: Update `docs/PRODUCTION_GUIDE.md` and `docs/PRODUCTION_GUIDE_CN.md`**

Line ~21 in both files mentions `SessionNoteTool` in the Context Management row. Replace that cell with:

```markdown
| **Context Management** | ✅ Persistent Markdown memory via MemoryManager (MEMORY.md + AGENTS.md); summarization when approaching context window limit |
```

(For the CN version, translate accordingly.)

- [ ] **Step 5: Update `docs/run_agent_analysis.md`**

Remove lines mentioning `SessionNoteTool` (around line 65 and line 233). Replace any architecture diagram references to SessionNoteTool with `MemoryManager`.

- [ ] **Step 6: Update `ARCHITECTURE_ANALYSIS.md`**

Lines ~204 and ~248 mention SessionNoteTool. Remove or replace with MemoryManager reference.

- [ ] **Step 7: Verify no stale references remain**

Run:
```bash
grep -rn "SessionNoteTool\|RecallNoteTool\|enable_note\|MINI_AGENT\.md" CLAUDE.md docs/ ARCHITECTURE_ANALYSIS.md 2>/dev/null | grep -v "DEVELOPMENT_GUIDE" | grep -v "2026-06-14-memory"
```

Expected: no output (the DEVELOPMENT_GUIDE 3.3 section is intentionally kept; the spec/design files are excluded).

- [ ] **Step 8: Commit**

```bash
git add CLAUDE.md docs/memory-system-design.md docs/PRODUCTION_GUIDE.md docs/PRODUCTION_GUIDE_CN.md docs/run_agent_analysis.md ARCHITECTURE_ANALYSIS.md
git commit -m "docs: update all docs for AGENTS.md rename and SessionNoteTool removal"
```

---

## Task 9: Add legacy file detection in cli.py

**Files:**
- Modify: `mini_agent/cli.py` (add detection after memory system init, ~line 640)

- [ ] **Step 1: Add detection logic**

After the memory system initialization block (around line 637-640):

```python
    # 4.5 Initialize persistent memory system
    memory_context = ""
    if config.tools.enable_memory:
        memory_manager = MemoryManager(workspace_dir)
        memory_context = memory_manager.initialize_memory()
        print(f"{Colors.GREEN}✅ Loaded memory system (dir: {memory_manager.memory_dir}){Colors.RESET}")
```

Add immediately after:

```python
    # 4.6 Legacy file detection (warn about deprecated files)
    _warn_legacy_files(workspace_dir)
```

- [ ] **Step 2: Define the helper function**

Add this function near the other helper functions in cli.py (e.g., before `add_workspace_tools` or after `_quiet_cleanup`):

```python
def _warn_legacy_files(workspace_dir: Path) -> None:
    """Print warnings for deprecated files from previous Mini-Agent versions."""
    # Check for global MINI_AGENT.md (renamed to AGENTS.md)
    legacy_global = Path.home() / ".mini-agent" / "MINI_AGENT.md"
    if legacy_global.exists():
        print(
            f"{Colors.YELLOW}⚠️  Detected {legacy_global}. "
            f"Please rename to AGENTS.md to match the new convention.{Colors.RESET}"
        )

    # Check for legacy SessionNoteTool JSON file
    legacy_notes = workspace_dir / ".agent_memory.json"
    if legacy_notes.exists():
        print(
            f"{Colors.YELLOW}ℹ️  Found {legacy_notes} (legacy SessionNoteTool data). "
            f"Safe to delete — SessionNoteTool has been removed.{Colors.RESET}"
        )
```

- [ ] **Step 3: Ensure `Path` is imported in cli.py**

Check that `from pathlib import Path` exists near the top of cli.py. If not, add it.

Run:
```bash
grep -n "from pathlib import Path" mini_agent/cli.py
```
Expected: a line number (already imported). If empty, add the import.

- [ ] **Step 4: Verify cli.py parses**

Run:
```bash
python -c "import ast; ast.parse(open('mini_agent/cli.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 5: Run full test suite**

Run:
```bash
uv run pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add mini_agent/cli.py
git commit -m "feat: add legacy file detection for MINI_AGENT.md and .agent_memory.json"
```

---

## Task 10: Final verification

- [ ] **Step 1: Run full test suite**

Run:
```bash
uv run pytest tests/ -v
```
Expected: All tests pass. No collection errors.

- [ ] **Step 2: Verify SessionNoteTool is fully gone from source**

Run:
```bash
grep -rn "SessionNoteTool\|RecallNoteTool\|note_tool\|enable_note" mini_agent/ tests/
```
Expected: no output (zero references in source/tests).

Note: `examples/03_session_notes.py` will still match — that's intentional (deprecated reference).

- [ ] **Step 3: Verify MINI_AGENT.md is gone from source**

Run:
```bash
grep -rn "MINI_AGENT\.md" mini_agent/ tests/
```
Expected: no output.

- [ ] **Step 4: Verify MCP memory is disabled**

Run:
```bash
python -c "import json; d=json.load(open('mini_agent/config/mcp.json')); print('memory disabled:', d['mcpServers']['memory']['disabled'])"
```
Expected: `memory disabled: True`

- [ ] **Step 5: Verify MemoryManager uses AGENTS.md**

Run:
```bash
python -c "from mini_agent.tools.memory_manager import MemoryManager; print(MemoryManager.PROJECT_INSTRUCTION_FILENAME, MemoryManager.GLOBAL_INSTRUCTION_FILE.name)"
```
Expected: `AGENTS.md AGENTS.md`

- [ ] **Step 6: Manual smoke test (optional)**

If a valid `config.yaml` exists, start the agent and confirm:
```bash
uv run python -m mini_agent.cli
```
Check the startup output includes:
- `✅ Loaded memory system (dir: ...)`
- No `✅ Loaded session note tool`
- No MCP memory server loading

If any legacy files exist, confirm warnings appear.

- [ ] **Step 7: Final commit (if any remaining changes)**

If verification surfaced any fixable issues, address and commit:
```bash
git add -A
git commit -m "chore: final fixes from verification pass"
```

If nothing to fix, this step is a no-op.

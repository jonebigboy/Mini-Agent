# `/init` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/init` slash command that makes Mini-Agent scan the current workspace and write an `AGENTS.md` file giving future sessions project-specific context.

**Architecture:** LLM-driven approach (Approach A from the design spec). The `/init` command injects a carefully crafted prompt as a normal user message into the existing agent execution loop — no new tools, no new execution model. The agent uses its existing Read/Bash/Write tools to explore the project and produce `AGENTS.md` at the workspace root.

**Tech Stack:** Python 3 (asyncio), prompt_toolkit, existing Mini-Agent toolchain.

**Spec:** `docs/superpowers/specs/2026-06-15-init-command-design.md`

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `mini_agent/commands/__init__.py` | Create | Empty package marker |
| `mini_agent/commands/init_command.py` | Create | Houses `INIT_PROMPT_TEMPLATE` constant (the only "logic" of this feature) |
| `mini_agent/cli.py` | Modify | Import the template, dispatch `/init` command, update help text and tab-completer |
| `tests/test_init_command.py` | Create | Unit test for the prompt template (format placeholder, key contents) |

**Why a separate `commands/` package:** `cli.py` is already 999 lines. Isolating the prompt in its own module keeps the command's "business logic" discoverable and gives the test a clean import target.

---

## Task 1: Create `commands` package with `INIT_PROMPT_TEMPLATE`

**Files:**
- Create: `mini_agent/commands/__init__.py`
- Create: `mini_agent/commands/init_command.py`
- Create: `tests/test_init_command.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_init_command.py`:

```python
"""Tests for the /init command prompt template."""

from mini_agent.commands.init_command import INIT_PROMPT_TEMPLATE


def test_template_is_non_empty_string():
    """The prompt template must be a non-empty string."""
    assert isinstance(INIT_PROMPT_TEMPLATE, str)
    assert len(INIT_PROMPT_TEMPLATE.strip()) > 0


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
    # Suggested scan targets
    assert "README.md" in result
    assert "pyproject.toml" in result
    assert "package.json" in result
    # Length constraint (Claude Code best practice)
    assert "60-300" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_init_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mini_agent.commands'`

- [ ] **Step 3: Create the package marker**

Create `mini_agent/commands/__init__.py` with this exact content:

```python
"""Slash-command implementations for Mini-Agent.

Each module in this package houses the prompt constant (and any helper
data) for a slash command that needs more than a one-liner. The actual
command dispatch lives in `mini_agent/cli.py`.
"""
```

- [ ] **Step 4: Create the prompt template module**

Create `mini_agent/commands/init_command.py` with this exact content:

```python
"""Prompt template for the `/init` slash command.

When the user types `/init`, cli.py substitutes this template with the
current workspace path and injects it as a normal user message. The agent
then uses its existing tools (Read/Bash/Write) to explore the project and
produce `<workspace>/AGENTS.md`.

Design references:
- Spec: docs/superpowers/specs/2026-06-15-init-command-design.md
- Inspired by Claude Code's /init command.
"""

INIT_PROMPT_TEMPLATE = """\
Please analyze the current workspace ({workspace}) and generate an \
AGENTS.md file at the workspace root to give future Agent sessions \
project-specific context.

Suggested exploration steps (adapt as needed):
1. Run `ls -la` to see the top-level structure.
2. Read README.md if it exists.
3. Read project manifest files: pyproject.toml / package.json / Cargo.toml / \
go.mod / pom.xml / build.gradle (whichever exists).
4. Read other AI assistant rule files if they exist: .cursorrules, \
.github/copilot-instructions.md, existing AGENTS.md (as reference only, \
do not copy).
5. Run `git log --oneline -10` and `git status` for project context.
6. Read entry-point source files as needed (e.g., src/main.py, lib/index.js).

Then use the Write tool to create {workspace}/AGENTS.md.

Output requirements:
- Language: match the primary language of README.md, or code comments if no README.
- Length: 60-300 lines total.
- Include these sections (order may vary):
  - Project Overview (1-3 sentences)
  - Development Commands (build, test, run, lint)
  - Architecture (core components, directory layout)
  - Key Conventions (code style, naming, commit rules)
  - Testing Strategy (how to run tests, test organization)
- DO NOT include: API documentation, standard language conventions, \
information easily inferred from code, frequently-changing info (e.g., \
version numbers).
- ONLY include: things a future Agent cannot infer directly from the code.

After writing, briefly report the file path and line count.
"""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_init_command.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add mini_agent/commands/__init__.py mini_agent/commands/init_command.py tests/test_init_command.py
git commit -m "feat(commands): add INIT_PROMPT_TEMPLATE for /init slash command

House the prompt that drives the upcoming /init command in its own
module so cli.py stays focused. Tested via simple format + content
checks; deeper behavior is verified end-to-end in later tasks."
```

---

## Task 2: Wire `/init` command into `cli.py` dispatch

**Files:**
- Modify: `mini_agent/cli.py` (add import near line 30-42; add `/init` branch around line 818)

**Key insight (simpler than spec §7 suggested):** The existing main loop already has the perfect structure for this. The command dispatch block is:

```python
if user_input.startswith("/"):
    command = user_input.lower()
    if command in ["/exit", ...]:
        ...
        break
    elif command == "/help":
        ...
        continue          # <- most branches continue
    ...
    else:
        # unknown command
        continue
```

For `/init`, we set `user_input` to the formatted prompt and **deliberately do NOT call `continue`**. Code then falls through to the "Normal conversation" section, which:
1. Checks `user_input.lower() in ["exit", "quit", "q"]` — the long prompt will not match, so no exit.
2. Proceeds to the agent execution block, which calls `agent.add_user_message(user_input)` and runs the agent with full Esc/confirmation/cleanup support.

No flag, no refactor, no duplicate code.

- [ ] **Step 1: Add the import**

In `mini_agent/cli.py`, find the existing tool imports around lines 35-42. After the `from mini_agent.tools.skill_tool import create_skill_tools` line (around line 39), add:

```python
from mini_agent.commands.init_command import INIT_PROMPT_TEMPLATE
```

The resulting import block should look like:

```python
from mini_agent.tools.base import Tool
from mini_agent.tools.bash_tool import BackgroundShellManager, BashKillTool, BashOutputTool, BashTool
from mini_agent.tools.file_tools import EditTool, ReadTool, WriteTool
from mini_agent.tools.mcp_loader import cleanup_mcp_connections, load_mcp_tools_async, set_mcp_timeout_config
from mini_agent.tools.memory_manager import MemoryManager
from mini_agent.tools.skill_tool import create_skill_tools
from mini_agent.commands.init_command import INIT_PROMPT_TEMPLATE
from mini_agent.utils import calculate_display_width
from mini_agent.security import SecurityMiddleware
from mini_agent.ui.confirm_selector import ConfirmSelector
```

- [ ] **Step 2: Add the `/init` command branch**

Find the `/bg` branch in the command dispatch (around lines 816-818):

```python
                elif command == "/bg":
                    _print_background_status()
                    continue

                else:
                    print(f"{Colors.RED}❌ Unknown command: {user_input}{Colors.RESET}")
                    print(f"{Colors.DIM}Type /help to see available commands{Colors.RESET}\n")
                    continue
```

Insert a new `elif` between `/bg` and the `else` branch:

```python
                elif command == "/bg":
                    _print_background_status()
                    continue

                elif command == "/init":
                    # Inject the init prompt as a normal user message and fall
                    # through to the regular agent execution flow below (which
                    # provides Esc cancellation, ConfirmSelector integration,
                    # background-process status printing, and error handling).
                    # No `continue` here — we want the fall-through.
                    user_input = INIT_PROMPT_TEMPLATE.format(workspace=str(workspace_dir))

                else:
                    print(f"{Colors.RED}❌ Unknown command: {user_input}{Colors.RESET}")
                    print(f"{Colors.DIM}Type /help to see available commands{Colors.RESET}\n")
                    continue
```

- [ ] **Step 3: Verify the fall-through works syntactically**

Run: `python -c "from mini_agent import cli; print('import OK')"`
Expected: `import OK` (no syntax errors, no import errors)

- [ ] **Step 4: Verify no regression in unit tests**

Run: `pytest tests/ -v --ignore=tests/test_session_integration.py --ignore=tests/test_integration.py`
Expected: All tests pass (we exclude the heavy integration tests; they're covered in Task 4's manual run).

If any test fails, investigate — the new import should not affect any existing behavior.

- [ ] **Step 5: Commit**

```bash
git add mini_agent/cli.py
git commit -m "feat(cli): wire /init command to inject init prompt

The /init branch formats INIT_PROMPT_TEMPLATE with the workspace path
and falls through to the normal agent execution flow, reusing the
existing Esc-cancellation, ConfirmSelector, and cleanup machinery."
```

---

## Task 3: Update `/help` text and tab-completer

**Files:**
- Modify: `mini_agent/cli.py` (print_help around line 202; WordCompleter around line 712)

- [ ] **Step 1: Add `/init` to the help text**

In `mini_agent/cli.py`, find the `print_help()` function's "Available Commands" section (around lines 196-204):

```python
    help_text = f"""
{Colors.BOLD}{Colors.BRIGHT_YELLOW}Available Commands:{Colors.RESET}
  {Colors.BRIGHT_GREEN}/help{Colors.RESET}      - Show this help message
  {Colors.BRIGHT_GREEN}/clear{Colors.RESET}     - Clear session history (keep system prompt)
  {Colors.BRIGHT_GREEN}/history{Colors.RESET}   - Show current session message count
  {Colors.BRIGHT_GREEN}/stats{Colors.RESET}     - Show session statistics
  {Colors.BRIGHT_GREEN}/log{Colors.RESET}       - Show log directory and recent files
  {Colors.BRIGHT_GREEN}/bg{Colors.RESET}        - 显示后台 shell 进程
  {Colors.BRIGHT_GREEN}/log <file>{Colors.RESET} - Read a specific log file
  {Colors.BRIGHT_GREEN}/exit{Colors.RESET}      - Exit program (also: exit, quit, q)
```

Insert the `/init` line after `/bg` (keep `/log <file>` after it for grouping with `/log`):

```python
    help_text = f"""
{Colors.BOLD}{Colors.BRIGHT_YELLOW}Available Commands:{Colors.RESET}
  {Colors.BRIGHT_GREEN}/help{Colors.RESET}      - Show this help message
  {Colors.BRIGHT_GREEN}/clear{Colors.RESET}     - Clear session history (keep system prompt)
  {Colors.BRIGHT_GREEN}/history{Colors.RESET}   - Show current session message count
  {Colors.BRIGHT_GREEN}/stats{Colors.RESET}     - Show session statistics
  {Colors.BRIGHT_GREEN}/init{Colors.RESET}      - Scan project and generate AGENTS.md
  {Colors.BRIGHT_GREEN}/log{Colors.RESET}       - Show log directory and recent files
  {Colors.BRIGHT_GREEN}/bg{Colors.RESET}        - 显示后台 shell 进程
  {Colors.BRIGHT_GREEN}/log <file>{Colors.RESET} - Read a specific log file
  {Colors.BRIGHT_GREEN}/exit{Colors.RESET}      - Exit program (also: exit, quit, q)
```

- [ ] **Step 2: Add `/init` to the WordCompleter**

Find the `command_completer` definition (around lines 711-715):

```python
    command_completer = WordCompleter(
        ["/help", "/clear", "/history", "/stats", "/log", "/bg", "/exit", "/quit", "/q"],
        ignore_case=True,
        sentence=True,
    )
```

Add `"/init"` to the list (insert after `"/stats"` to mirror the help-text order):

```python
    command_completer = WordCompleter(
        ["/help", "/clear", "/history", "/stats", "/init", "/log", "/bg", "/exit", "/quit", "/q"],
        ignore_case=True,
        sentence=True,
    )
```

- [ ] **Step 3: Verify cli.py still imports cleanly**

Run: `python -c "from mini_agent import cli; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Verify help text renders without breaking alignment**

Run: `python -c "from mini_agent.cli import print_help; print_help()"`
Expected: Help text prints; `/init` line is visible and aligned with other commands.

- [ ] **Step 5: Commit**

```bash
git add mini_agent/cli.py
git commit -m "feat(cli): add /init to help text and tab-completer

Surface the new command in /help output and WordCompleter so users can
discover and tab-complete it."
```

---

## Task 4: End-to-end manual verification

This task has no code changes — it validates the feature works as designed and the spec's acceptance criteria are met. Do not skip.

**Files:** None modified.

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass, including the new `tests/test_init_command.py`.

If integration tests fail due to environment (API keys, etc.), document which fail and why — only count failures caused by this change as blockers.

- [ ] **Step 2: Launch Mini-Agent and verify `/help`**

Run: `uv run python -m mini_agent.cli`
Type: `/help`
Expected:
- `/init` line appears in the "Available Commands" section
- Description: "Scan project and generate AGENTS.md"
- Alignment matches sibling commands

Type: `/exit` to quit.

- [ ] **Step 3: Verify tab-completion**

Run: `uv run python -m mini_agent.cli`
Type (do NOT press Enter): `/i`
Press: `Tab`
Expected: Auto-completes to `/init` (or shows it as a completion option).

Type: `/exit` to quit.

- [ ] **Step 4: Backup any existing AGENTS.md in the test workspace**

The Mini-Agent repo itself has an `AGENTS.md`? Let's check.

Run: `ls -la AGENTS.md CLAUDE.md 2>/dev/null`
Expected: Lists whatever project-context files exist.

If `AGENTS.md` exists, back it up so we can compare the regenerated version:

```bash
cp AGENTS.md AGENTS.md.bak.$(date +%s)
```

- [ ] **Step 5: Run `/init` in the Mini-Agent repo (self-test)**

Run: `uv run python -m mini_agent.cli`
Type: `/init`
Press: `Enter`

Expected behavior:
1. Agent starts executing (visible "Thinking..." indicator)
2. Tool calls are visible: Read README.md, Bash `ls -la`, Read pyproject.toml, Bash `git log`, etc.
3. Agent eventually calls Write with path `.../AGENTS.md`
4. Agent prints a closing message with file path and line count

Press: `/exit` to quit when done.

- [ ] **Step 6: Verify the output AGENTS.md**

Run: `wc -l AGENTS.md && head -30 AGENTS.md`
Expected:
- Line count is between 60 and 300
- Content includes sections resembling: Project Overview, Development Commands, Architecture, Key Conventions, Testing Strategy
- Language matches the project (Chinese for Mini-Agent, given the Chinese CLAUDE.md)
- Mentions `uv sync`, `pytest`, `mini-agent` command

- [ ] **Step 7: Compare with previous AGENTS.md (if backup exists)**

If Step 4 created a backup:
Run: `diff AGENTS.md.bak.* AGENTS.md | head -50`

Acceptable outcomes:
- New version has same key information (build commands, architecture)
- Wording/structure may differ — that's fine, the LLM has its own style
- If the new version is missing critical info (e.g., no `uv sync`), note it for prompt iteration

- [ ] **Step 8: Test cancellation via Esc**

Run: `uv run python -m mini_agent.cli`
Type: `/init`
Press: `Enter`
Once "Thinking..." appears, press: `Esc`
Expected: Agent execution stops; control returns to the prompt. No partial file write should corrupt AGENTS.md (but if a partial write happens, the backup from Step 4 covers you).

Type: `/exit` to quit.

- [ ] **Step 9: Test in a simple Python project (cross-project sanity check)**

Create a tiny test project in a temp dir:

```bash
mkdir -p /tmp/init-test-project/src
cat > /tmp/init-test-project/pyproject.toml <<'EOF'
[project]
name = "test-project"
version = "0.1.0"
dependencies = ["requests"]

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF

cat > /tmp/init-test-project/README.md <<'EOF'
# Test Project
A minimal Python project for testing /init.
EOF

cat > /tmp/init-test-project/src/main.py <<'EOF'
def main():
    print("hello")

if __name__ == "__main__":
    main()
EOF
```

Run: `uv run python -m mini_agent.cli --workspace /tmp/init-test-project`
Type: `/init`
Press: `Enter`
Wait for completion.
Type: `/exit`

Run: `wc -l /tmp/init-test-project/AGENTS.md && cat /tmp/init-test-project/AGENTS.md`
Expected:
- File exists
- Line count 60-300
- Contains "Test Project" or "test-project"
- Mentions Python, pytest
- Language matches README (English, since the test README is English)

- [ ] **Step 10: Clean up test artifacts**

```bash
# Remove the test project
rm -rf /tmp/init-test-project

# Decide what to do with Mini-Agent's own AGENTS.md
ls AGENTS.md.bak.* 2>/dev/null
```

Decision point:
- If the new AGENTS.md is good: `rm AGENTS.md.bak.*` (keep new version)
- If the new AGENTS.md is worse: `mv AGENTS.md.bak.<timestamp> AGENTS.md` (restore previous)
- If unsure: leave both for the user to decide

Do NOT commit the regenerated AGENTS.md automatically — that's a user decision. Leave the working tree as-is.

- [ ] **Step 11: Final commit (only if test artifacts leaked into the repo)**

Run: `git status`
If only `AGENTS.md` shows as modified and you've decided to keep the new version:

```bash
git add AGENTS.md
git commit -m "docs: regenerate AGENTS.md via /init command

Output of running /init on the Mini-Agent repo itself, demonstrating
the new command's behavior. Manually reviewed."
```

If `AGENTS.md` should not change, restore it: `git checkout AGENTS.md`.

If nothing shows as modified, no commit needed.

---

## Spec Coverage Self-Check

After writing the plan, I verified each spec section maps to tasks:

| Spec Section | Coverage |
|---|---|
| §1 目标 | Tasks 1-3 implement the command; Task 4 verifies it |
| §2.1 业界参考 | No code required; informs the prompt (Task 1) |
| §3 决策 #1 LLM 驱动 | Tasks 1-2 (inject prompt, no new tools) |
| §3 决策 #2 仅斜杠命令 | Tasks 2-3 (no `mini-agent init` subcommand added) |
| §3 决策 #3 不检查、直接覆写 | Task 4 Step 4 backs up existing AGENTS.md manually (no auto-check) |
| §3 决策 #4 输出路径 `<workspace>/AGENTS.md` | Task 1 prompt: `{workspace}/AGENTS.md` |
| §3 决策 #5 自动推断语言 | Task 1 prompt: "match the primary language of README.md" |
| §3 决策 #6 标准章节方向 | Task 1 prompt: 5 sections listed |
| §3 决策 #7 60-300 行 | Task 1 prompt + Task 4 Step 6 verifies |
| §3 决策 #8 提示词独立模块 | Task 1 creates `mini_agent/commands/init_command.py` |
| §3 决策 #9 沿用 Esc 取消 | Task 2 Step 2 comment + Task 4 Step 8 verifies |
| §3 决策 #10 token 管理 | No code; relies on existing agent infrastructure |
| §4.1 数据流 | Task 2 implements the fall-through |
| §5 提示词设计 | Task 1 Step 4 contains the exact prompt |
| §6.1 新增 2 文件 | Task 1 |
| §6.2 cli.py 4 改动 | Task 2 (import + branch) + Task 3 (help + completer) |
| §6.3 不修改 | Plan does not touch config.yaml, system_prompt.md, agent.py, tools/ |
| §7 实现策略 | Task 2 Step 2 implements the simplified fall-through (better than spec's option (a)/(b)) |
| §8 边缘情况 | Task 4 Steps 8-9 cover cancellation and small-project behavior |
| §10 验收标准 | Task 4 maps to all bullets |

---

## Notes for the Implementer

1. **Spec §7 said "二选一"** (flag vs. duplicate code). The plan's Task 2 Step 2 found a third, simpler path: pure fall-through with no flag. This is cleaner than either option in the spec. If a reviewer objects, the spec's option (a) is the fallback.

2. **Do not auto-commit AGENTS.md.** Task 4 Step 11 makes this explicit. The user decides whether to replace the existing project context file.

3. **Prompt iteration is expected.** Task 4 Step 7 includes a comparison step. If the regenerated AGENTS.md is missing key info, treat it as a prompt-quality issue — edit `INIT_PROMPT_TEMPLATE` in `mini_agent/commands/init_command.py` and rerun.

4. **No new tests beyond `test_init_command.py`.** The spec explicitly says "无新增测试，回归保护" (no new tests, regression protection). The four tests in Task 1 cover the only pure-logic artifact (the template). Everything else is integration with the existing agent, validated manually in Task 4.

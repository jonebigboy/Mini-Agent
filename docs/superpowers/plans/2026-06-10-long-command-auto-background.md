# Long Command Auto-Background Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect long-running commands (pip install, npm install, etc.) and automatically execute them with real-time progress hints, so users no longer see the CLI "freeze" during package installations.

**Architecture:** Add a `_is_long_command()` method to `BashTool` that matches command strings against known long-command patterns. When detected, the foreground execution path transparently switches to a polling loop that prints progress hints to the terminal. The method signature and return type (`BashOutputResult`) remain identical, so `Agent` and `CLI` layers need zero changes. Output is truncated to 30K characters (10K head + 10K tail) to prevent token overflow.

**Tech Stack:** Python 3.10+, asyncio, `asyncio.create_subprocess_shell`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `mini_agent/tools/bash_tool.py` | Modify | Add `LONG_COMMAND_PATTERNS`, `_C_*` color constants, `_is_long_command()`, `_truncate_output()`, `_execute_long_command()` to `BashTool` |
| `tests/test_bash_tool.py` | Modify | Add tests for long command detection, output truncation, and integration |

No new files needed. All changes fit within existing files.

---

### Task 1: Add long command pattern detection

**Files:**
- Modify: `mini_agent/tools/bash_tool.py` (add `import os` at top, add `LONG_COMMAND_PATTERNS` at module level, add `_is_long_command` method to `BashTool`)
- Test: `tests/test_bash_tool.py`

- [ ] **Step 1: Write the failing test for `_is_long_command`**

Add to `tests/test_bash_tool.py` at the end of the file:

```python
# === Long Command Detection Tests ===


def test_is_long_command_pip():
    """Test that pip install is detected as a long command."""
    bash_tool = BashTool()
    assert bash_tool._is_long_command("pip install torch")
    assert bash_tool._is_long_command("pip3 install -r requirements.txt")
    assert bash_tool._is_long_command("uv pip install sentence-transformers chromadb")


def test_is_long_command_npm():
    """Test that npm install is detected as a long command."""
    bash_tool = BashTool()
    assert bash_tool._is_long_command("npm install")
    assert bash_tool._is_long_command("npm ci")
    assert bash_tool._is_long_command("yarn add react")
    assert bash_tool._is_long_command("pnpm install --frozen-lockfile")


def test_is_long_command_docker():
    """Test that docker build/pull is detected as a long command."""
    bash_tool = BashTool()
    assert bash_tool._is_long_command("docker build -t myapp .")
    assert bash_tool._is_long_command("docker pull python:3.12")


def test_is_long_command_negative():
    """Test that short commands are NOT detected as long commands."""
    bash_tool = BashTool()
    assert not bash_tool._is_long_command("echo hello")
    assert not bash_tool._is_long_command("git status")
    assert not bash_tool._is_long_command("ls -la")
    assert not bash_tool._is_long_command("python --version")
    assert not bash_tool._is_long_command("cat file.txt")


def test_is_long_command_edge_cases():
    """Test edge cases: empty string, compound commands."""
    bash_tool = BashTool()
    assert not bash_tool._is_long_command("")
    assert bash_tool._is_long_command("echo setup && pip install torch")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "test_is_long_command" -v`

Expected: FAIL — `AttributeError: 'BashTool' object has no attribute '_is_long_command'`

- [ ] **Step 3: Implement `_is_long_command` and the pattern list**

In `mini_agent/tools/bash_tool.py`:

**3a.** Add `import os` to the existing imports at the top (after `import re` on line 11):

```python
import os
```

**3b.** Add the following at module level, after the `if TYPE_CHECKING:` block (after line 21):

```python
# Regex patterns for commands known to take a long time (installs, builds, etc.)
LONG_COMMAND_PATTERNS = [
    r'\b(pip|pip3|uv\s+pip)\s+install\b',
    r'\b(npm|yarn|pnpm)\s+(install|ci|add)\b',
    r'\bdocker\s+(build|pull)\b',
    r'\bcargo\s+(build|install)\b',
    r'\bgo\s+(install|mod\s+(download|tidy))\b',
    r'\bmake\s',
    r'\bbrew\s+install\b',
    r'\b(apt|yum|dnf)\s+install\b',
    r'\bbundle\s+install\b',
]
```

**3c.** Add the `_is_long_command` method to the `BashTool` class (after `__init__`, around line 243):

```python
    def _is_long_command(self, command: str) -> bool:
        """Check if a command is likely to take a long time.

        Matches against known patterns like pip install, npm install, docker build, etc.
        """
        if not command or not command.strip():
            return False
        for pattern in LONG_COMMAND_PATTERNS:
            if re.search(pattern, command):
                return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "test_is_long_command" -v`

Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: add long command pattern detection for auto-background execution"
```

---

### Task 2: Add output truncation utility

**Files:**
- Modify: `mini_agent/tools/bash_tool.py` (add `_truncate_output` method to `BashTool`)
- Test: `tests/test_bash_tool.py`

- [ ] **Step 1: Write the failing test for `_truncate_output`**

Add to `tests/test_bash_tool.py`:

```python
def test_truncate_output_short():
    """Short output should not be truncated."""
    bash_tool = BashTool()
    text = "Hello world"
    assert bash_tool._truncate_output(text) == "Hello world"


def test_truncate_output_exact_limit():
    """Output at exactly 30000 chars should not be truncated."""
    bash_tool = BashTool()
    text = "x" * 30000
    assert bash_tool._truncate_output(text) == text


def test_truncate_output_over_limit():
    """Output over 30000 chars should be truncated to head + tail."""
    bash_tool = BashTool()
    text = "A" * 10001 + "B" * 10000 + "C" * 10000  # 30001 chars
    result = bash_tool._truncate_output(text)
    assert len(result) < len(text)
    assert result.startswith("A" * 10001)
    assert result.endswith("C" * 10000)
    assert "... truncated" in result


def test_truncate_output_multiline():
    """Truncation should work correctly with multiline output."""
    bash_tool = BashTool()
    lines = [f"Line {i}: " + "x" * 100 for i in range(400)]  # ~40000+ chars
    text = "\n".join(lines)
    result = bash_tool._truncate_output(text)
    assert len(result) < len(text)
    assert result.startswith("Line 0:")
    assert "truncated" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "test_truncate_output" -v`

Expected: FAIL — `AttributeError: 'BashTool' object has no attribute '_truncate_output'`

- [ ] **Step 3: Implement `_truncate_output`**

Add this method to the `BashTool` class in `mini_agent/tools/bash_tool.py` (right after `_is_long_command`):

```python
    def _truncate_output(self, text: str, max_chars: int = 30000, head_chars: int = 10000, tail_chars: int = 10000) -> str:
        """Truncate output to prevent token overflow.

        If text exceeds max_chars, keeps head_chars from the start and tail_chars from the end,
        with a truncation notice in the middle.
        """
        if len(text) <= max_chars:
            return text
        head = text[:head_chars]
        tail = text[-tail_chars:]
        truncated_count = len(text) - head_chars - tail_chars
        return f"{head}\n\n... [{truncated_count} characters truncated] ...\n\n{tail}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "test_truncate_output" -v`

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: add output truncation utility for long command results"
```

---

### Task 3: Implement `_execute_long_command` method

**Files:**
- Modify: `mini_agent/tools/bash_tool.py` (add `_C_*` color constants at module level, add `_execute_long_command` method to `BashTool`)
- Test: `tests/test_bash_tool.py`

- [ ] **Step 1: Write the failing test for `_execute_long_command`**

Add to `tests/test_bash_tool.py`:

```python
@pytest.mark.asyncio
async def test_execute_long_command_success():
    """Test that a long command runs and returns output correctly."""
    bash_tool = BashTool()
    result = await bash_tool._execute_long_command(
        command="echo 'hello from long command'",
        timeout=60,
    )
    assert isinstance(result, BashOutputResult)
    assert result.success
    assert "hello from long command" in result.stdout


@pytest.mark.asyncio
async def test_execute_long_command_timeout():
    """Test that a long command respects timeout."""
    bash_tool = BashTool()
    result = await bash_tool._execute_long_command(
        command="sleep 100",
        timeout=1,
    )
    assert not result.success
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_long_command_captures_multiline():
    """Test that long command captures multiple lines of output."""
    bash_tool = BashTool()
    result = await bash_tool._execute_long_command(
        command="echo 'line1' && echo 'line2' && echo 'line3'",
        timeout=30,
    )
    assert result.success
    assert "line1" in result.stdout
    assert "line2" in result.stdout
    assert "line3" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "test_execute_long_command" -v`

Expected: FAIL — `AttributeError: 'BashTool' object has no attribute '_execute_long_command'`

- [ ] **Step 3: Add color constants and implement `_execute_long_command`**

**3a.** Add these ANSI color constants at module level in `mini_agent/tools/bash_tool.py` (after the `LONG_COMMAND_PATTERNS` list from Task 1):

```python
# ANSI color constants for progress hints in _execute_long_command
_C_RESET = "\033[0m"
_C_DIM = "\033[2m"
_C_BRIGHT_RED = "\033[91m"
_C_BRIGHT_GREEN = "\033[92m"
_C_BRIGHT_YELLOW = "\033[93m"
```

**3b.** Add the `_execute_long_command` method to the `BashTool` class (right after `_truncate_output`):

```python
    async def _execute_long_command(
        self,
        command: str,
        timeout: int = 600,
    ) -> BashOutputResult:
        """Execute a long-running command with real-time progress hints.

        Starts the command as a subprocess with combined stdout/stderr,
        then polls for output lines while printing progress hints to the terminal.
        Returns the full output when done — same return type as foreground execution.
        """
        if timeout < 600:
            timeout = 600

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"

        if self.is_windows:
            process = await asyncio.create_subprocess_exec(
                "powershell.exe", "-NoProfile", "-Command", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.workspace_dir,
                env=env,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.workspace_dir,
                env=env,
            )

        output_lines: list[str] = []
        start_time = time.time()
        last_output_time = start_time
        last_hint_time = start_time
        hint_interval = 5.0
        poll_interval = 0.2

        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    process.kill()
                    error_msg = f"Command timed out after {timeout} seconds"
                    print(f"\n   {_C_BRIGHT_RED}{error_msg}{_C_RESET}")
                    return BashOutputResult(
                        success=False,
                        error=error_msg,
                        stdout="\n".join(output_lines),
                        stderr="",
                        exit_code=-1,
                    )

                try:
                    line_bytes = await asyncio.wait_for(
                        process.stdout.readline(), timeout=poll_interval
                    )
                    if line_bytes:
                        line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
                        output_lines.append(line)
                        last_output_time = time.time()
                        print(f"   {_C_DIM}{line}{_C_RESET}")
                    else:
                        break
                except asyncio.TimeoutError:
                    pass

                if process.returncode is not None:
                    remaining = await process.stdout.read()
                    if remaining:
                        for line in remaining.decode("utf-8", errors="replace").splitlines():
                            output_lines.append(line)
                            print(f"   {_C_DIM}{line}{_C_RESET}")
                    break

                now = time.time()
                if now - last_hint_time >= hint_interval and now - last_output_time >= hint_interval:
                    elapsed_str = f"{now - start_time:.0f}"
                    print(f"   {_C_BRIGHT_YELLOW}⏳ 仍在执行中... (已用时 {elapsed_str}s){_C_RESET}")
                    last_hint_time = now

            await process.wait()
            exit_code = process.returncode

            full_output = "\n".join(output_lines)
            truncated_output = self._truncate_output(full_output)

            is_success = exit_code == 0
            elapsed = time.time() - start_time

            if is_success:
                print(f"   {_C_BRIGHT_GREEN}✓ 命令完成 (耗时 {elapsed:.1f}s){_C_RESET}")
            else:
                print(f"   {_C_BRIGHT_RED}✗ 命令失败，退出码 {exit_code} (耗时 {elapsed:.1f}s){_C_RESET}")

            error_msg = None
            if not is_success:
                error_msg = f"Command failed with exit code {exit_code}"
                if truncated_output:
                    last_lines = "\n".join(truncated_output.split("\n")[-5:])
                    error_msg += f"\n{last_lines}"

            return BashOutputResult(
                success=is_success,
                error=error_msg,
                stdout=truncated_output,
                stderr="",
                exit_code=exit_code or 0,
            )

        except Exception as e:
            if process.returncode is None:
                process.kill()
            return BashOutputResult(
                success=False,
                error=str(e),
                stdout="\n".join(output_lines),
                stderr="",
                exit_code=-1,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "test_execute_long_command" -v`

Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: add _execute_long_command with progress hints and output streaming"
```

---

### Task 4: Wire long command detection into `execute()`

**Files:**
- Modify: `mini_agent/tools/bash_tool.py` (lines 407-457, the foreground execution `else` branch)
- Test: `tests/test_bash_tool.py`

- [ ] **Step 1: Write the integration test**

Add to `tests/test_bash_tool.py`:

```python
@pytest.mark.asyncio
async def test_long_command_auto_detected():
    """Test that a pip install-like command is auto-detected and runs via _execute_long_command."""
    bash_tool = BashTool()
    result = await bash_tool.execute(
        command="pip install --dry-run nonexistent-pkg-xyz-12345 || echo 'done'"
    )
    assert result.success
    assert len(result.stdout) > 0 or result.exit_code == 0


@pytest.mark.asyncio
async def test_short_command_unchanged():
    """Test that short commands still use the original communicate() path."""
    bash_tool = BashTool()
    result = await bash_tool.execute(command="echo 'short command'")
    assert result.success
    assert "short command" in result.stdout
    assert isinstance(result.stderr, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py::test_long_command_auto_detected tests/test_bash_tool.py::test_short_command_unchanged -v`

Expected: `test_long_command_auto_detected` may fail because `execute()` doesn't route to `_execute_long_command` yet.

- [ ] **Step 3: Modify the `execute()` method's foreground branch**

In `mini_agent/tools/bash_tool.py`, add 2 lines at the beginning of the `else:` block (line 407) to check for long commands before the existing foreground logic:

Change the `else:` block from:
```python
            else:
                # Foreground execution: Create isolated process
```

To:
```python
            else:
                # Check if this is a long-running command that needs special handling
                if self._is_long_command(command):
                    return await self._execute_long_command(command, timeout)

                # Foreground execution: Create isolated process
```

The rest of the foreground code (subprocess creation, communicate, etc.) remains completely unchanged.

- [ ] **Step 4: Run ALL bash tool tests to verify nothing is broken**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -v`

Expected: All existing tests + new tests PASS

- [ ] **Step 5: Commit**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: wire long command detection into BashTool.execute()"
```

---

### Task 5: Run full test suite and verify no regressions

**Files:**
- No file changes — verification only

- [ ] **Step 1: Run the complete test suite**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/ -v`

Expected: All tests PASS.

- [ ] **Step 2: Manual smoke test**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run python -c "
from mini_agent.tools.bash_tool import BashTool, LONG_COMMAND_PATTERNS

tool = BashTool()

assert tool._is_long_command('pip install torch'), 'pip install should be long'
assert tool._is_long_command('uv pip install chromadb'), 'uv pip install should be long'
assert not tool._is_long_command('echo hello'), 'echo should not be long'

long_text = 'x' * 50000
truncated = tool._truncate_output(long_text)
assert len(truncated) < len(long_text), 'should truncate'
assert 'truncated' in truncated, 'should have truncation notice'

print('All smoke tests passed!')
"`

Expected: `All smoke tests passed!`

- [ ] **Step 3: Final commit (only if fixes were needed during testing)**

```bash
git add -A
git commit -m "fix: minor adjustments from smoke testing"
```

Skip this step if no fixes were needed.

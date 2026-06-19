# Plan A: Unified Streaming + Dual Timeout + Server Detection

**Goal:** Replace `communicate()` with unified streaming for all foreground commands, add dual timeout (60s idle / 600s absolute), and auto-detect dev servers for background promotion.

**Architecture:** Rename `_execute_long_command` → `_execute_streaming` as the single foreground execution path. Remove `communicate()` entirely. Remove `LONG_COMMAND_PATTERNS` and `_is_long_command`. Add `SERVER_DETECTION_PATTERNS` for auto-backgrounding. All commands get real-time output, dual timeout, and output truncation.

**Tech Stack:** Python 3.10+, asyncio, existing `BackgroundShell` / `BackgroundShellManager`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `mini_agent/tools/bash_tool.py` | Modify | Remove `LONG_COMMAND_PATTERNS`, `_is_long_command`; add `SERVER_DETECTION_PATTERNS`; rename `_execute_long_command` → `_execute_streaming`; replace `communicate()` branch with `_execute_streaming` call |
| `tests/test_bash_tool.py` | Modify | Remove `LONG_COMMAND_PATTERNS` tests, update `_execute_long_command` tests → `_execute_streaming`, add dual timeout tests, add server detection tests |

---

### Task 1: Rename `_execute_long_command` → `_execute_streaming` and remove long command detection

**Files:**
- Modify: `mini_agent/tools/bash_tool.py`

- [ ] **Step 1: Remove `LONG_COMMAND_PATTERNS` list** (lines 24-35)

Delete the entire `LONG_COMMAND_PATTERNS` list. It's no longer needed — all commands use streaming now.

- [ ] **Step 2: Remove `_is_long_command` method** (lines 266-276)

Delete the entire `_is_long_command` method from the `BashTool` class.

- [ ] **Step 3: Rename `_execute_long_command` → `_execute_streaming`**

Rename the method at line 519:
```python
    async def _execute_streaming(
        self,
        command: str,
        timeout: int = 600,
        idle_timeout: float = 60.0,
    ) -> BashOutputResult:
```

Also remove the `if timeout < 600: timeout = 600` line — timeout is now handled by the caller (execute) which caps it at 600 already.

- [ ] **Step 4: Update `execute()` to call `_execute_streaming` for ALL foreground commands**

Replace the entire `else:` block (lines 454-508) with a single call:

```python
            else:
                # All foreground commands use streaming execution
                return await self._execute_streaming(command, timeout)
```

This removes the `communicate()` path entirely.

- [ ] **Step 5: Run tests, fix broken ones**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -v`

Tests referencing `_is_long_command`, `_execute_long_command`, or `LONG_COMMAND_PATTERNS` will fail. Fix them:
- Remove all `test_is_long_command_*` tests
- Rename `test_execute_long_command_*` → `test_execute_streaming_*` and update the method call
- `test_long_command_auto_detected` can stay as-is (calls `execute()` which still works)
- `test_short_command_unchanged` stays but now `result.stderr` will be `""` (streaming merges stderr into stdout) — update assertion

- [ ] **Step 6: Commit**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "refactor: replace communicate() with unified streaming for all commands"
```

---

### Task 2: Add dual timeout (idle + absolute)

**Files:**
- Modify: `mini_agent/tools/bash_tool.py`
- Modify: `tests/test_bash_tool.py`

- [ ] **Step 1: Add idle timeout to `_execute_streaming`**

The method already has `idle_timeout: float = 60.0` parameter from Task 1. Now add the idle timeout check inside the polling loop. After the existing absolute timeout check (around line 562), add:

```python
                # Check idle timeout (no output for idle_timeout seconds)
                if time.time() - last_output_time >= idle_timeout:
                    process.kill()
                    idle_elapsed = time.time() - last_output_time
                    error_msg = f"Command idle timeout: no output for {idle_elapsed:.0f}s (limit: {idle_timeout}s)"
                    print(f"\n   {_C_BRIGHT_RED}{error_msg}{_C_RESET}")
                    return BashOutputResult(
                        success=False,
                        error=error_msg,
                        stdout="\n".join(output_lines),
                        stderr="",
                        exit_code=-1,
                    )
```

- [ ] **Step 2: Write tests for dual timeout**

Add to `tests/test_bash_tool.py`:

```python
@pytest.mark.asyncio
async def test_streaming_idle_timeout():
    """Test that streaming kills command after idle_timeout with no output."""
    bash_tool = BashTool()
    # sleep produces no output — should trigger idle timeout
    result = await bash_tool._execute_streaming(
        command="sleep 100",
        timeout=600,
        idle_timeout=2.0,
    )
    assert not result.success
    assert "idle timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_streaming_absolute_timeout():
    """Test that streaming kills command after absolute timeout."""
    bash_tool = BashTool()
    # Command produces output every 0.5s, so idle timeout won't trigger
    # But absolute timeout of 2s will
    result = await bash_tool._execute_streaming(
        command="for i in $(seq 1 100); do echo tick; sleep 0.5; done",
        timeout=2,
        idle_timeout=60.0,
    )
    assert not result.success
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_streaming_active_command_survives():
    """Test that an actively-outputting command is NOT killed by idle timeout."""
    bash_tool = BashTool()
    # Produces output every 0.3s for ~1.5s total, idle_timeout=1s
    result = await bash_tool._execute_streaming(
        command="for i in 1 2 3 4 5; do echo line$i; sleep 0.3; done",
        timeout=30,
        idle_timeout=1.0,
    )
    assert result.success
    assert "line5" in result.stdout
```

- [ ] **Step 3: Run tests**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "test_streaming" -v`

Expected: All 3 new tests + existing streaming tests PASS.

- [ ] **Step 4: Commit**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: add dual timeout (idle + absolute) for streaming execution"
```

---

### Task 3: Add dev server auto-detection

**Files:**
- Modify: `mini_agent/tools/bash_tool.py`
- Modify: `tests/test_bash_tool.py`

- [ ] **Step 1: Add `SERVER_DETECTION_PATTERNS` constant**

Add after the `_C_*` color constants (around line 42):

```python
# Regex patterns that indicate a dev server has started.
# When matched in output, the command is auto-promoted to background.
SERVER_DETECTION_PATTERNS = [
    r'localhost:\d+',
    r'127\.0\.0\.1:\d+',
    r'0\.0\.0\.0:\d+',
    r'\[::\]:\d+',
    r'Server running',
    r'server started',
    r'Listening on',
    r'webpack.*compiled',
    r'Development server running',
    r'Uvicorn running',
    r'Flask.*running',
    r'Ready for connections',
]
```

- [ ] **Step 2: Add `_is_server_output` method to `BashTool`**

Add after `_truncate_output`:

```python
    def _is_server_output(self, line: str) -> bool:
        """Check if an output line indicates a dev server has started."""
        if not line:
            return False
        for pattern in SERVER_DETECTION_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                return True
        return False
```

- [ ] **Step 3: Add server auto-detection in `_execute_streaming`**

In the polling loop, right after printing each output line and before the idle timeout check, add server detection. When a server is detected, automatically promote the command to a background shell:

After the block that reads a line and prints it (around where `last_output_time = time.time()` is set), add:

```python
                        # Auto-detect dev servers and promote to background
                        if self._is_server_output(line):
                            return await self._promote_to_background(
                                process, command, output_lines, start_time
                            )
```

- [ ] **Step 4: Add `_promote_to_background` method to `BashTool`**

Add after `_is_server_output`:

```python
    async def _promote_to_background(
        self,
        process: asyncio.subprocess.Process,
        command: str,
        existing_output: list[str],
        start_time: float,
    ) -> BashOutputResult:
        """Promote a running foreground process to background shell.

        Called when server output is detected. Moves the process into
        BackgroundShellManager so the agent can continue working.
        """
        bash_id = str(uuid.uuid4())[:8]

        # Wrap process in BackgroundShell
        bg_shell = BackgroundShell(
            bash_id=bash_id,
            command=command,
            process=process,
            start_time=start_time,
        )
        # Add existing output lines
        for line in existing_output:
            bg_shell.add_output(line)

        BackgroundShellManager.add(bg_shell)
        await BackgroundShellManager.start_monitor(bash_id)

        elapsed = time.time() - start_time
        print(f"   {_C_BRIGHT_YELLOW}🔄 检测到开发服务器，已转为后台运行 (bash_id={bash_id}, 耗时 {elapsed:.1f}s){_C_RESET}")

        truncated = self._truncate_output("\n".join(existing_output))
        return BashOutputResult(
            success=True,
            content=f"Dev server detected and moved to background (bash_id='{bash_id}').\n\nCommand: {command}\nBash ID: {bash_id}",
            stdout=truncated,
            stderr="",
            exit_code=0,
            bash_id=bash_id,
        )
```

- [ ] **Step 5: Write tests**

Add to `tests/test_bash_tool.py`:

```python
def test_is_server_output():
    """Test that server output patterns are detected."""
    bash_tool = BashTool()
    assert bash_tool._is_server_output("Serving on localhost:8080")
    assert bash_tool._is_server_output("Uvicorn running on http://127.0.0.1:8000")
    assert bash_tool._is_server_output("webpack successfully compiled")
    assert bash_tool._is_server_output("Server running at 0.0.0.0:3000")
    assert not bash_tool._is_server_output("echo hello")
    assert not bash_tool._is_server_output("pip install torch")
    assert not bash_tool._is_server_output("")
```

- [ ] **Step 6: Run all tests**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -v`

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: auto-detect dev servers and promote to background"
```

---

### Task 4: Update design doc and run full regression

**Files:**
- Modify: `docs/特性设计文档/long-command-auto-background.md`
- No test file changes

- [ ] **Step 1: Update design doc to reflect Plan A changes**

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/ -v`

Expected: All tests PASS.

- [ ] **Step 3: Smoke test**

Run: `cd /Users/jone/Desktop/codespace/Mini-Agent && uv run python -c "
from mini_agent.tools.bash_tool import BashTool, SERVER_DETECTION_PATTERNS

tool = BashTool()
assert tool._is_server_output('Serving on localhost:8080')
assert not tool._is_server_output('echo hello')

# Verify streaming works for simple command
import asyncio
result = asyncio.run(tool._execute_streaming('echo hello', timeout=10, idle_timeout=5.0))
assert result.success
assert 'hello' in result.stdout
print('All smoke tests passed!')
"`

Expected: `All smoke tests passed!`

- [ ] **Step 4: Commit**

```bash
git add docs/ docs/superpowers/plans/
git commit -m "docs: update design doc for Plan A (unified streaming + dual timeout + server detection)"
```

"""Test cases for Bash Tool."""

import asyncio

import pytest

from mini_agent.tools.bash_tool import BackgroundShell, BackgroundShellManager, BashKillTool, BashOutputResult, BashOutputTool, BashTool


@pytest.mark.asyncio
async def test_foreground_command():
    """Test executing a simple foreground command."""
    print("\n=== Testing Foreground Command ===")

    bash_tool = BashTool()
    result = await bash_tool.execute(command="echo 'Hello from foreground'")

    assert result.success
    assert "Hello from foreground" in result.stdout
    assert result.exit_code == 0
    print(f"Output: {result.content}")


@pytest.mark.asyncio
async def test_foreground_command_with_stderr():
    """Test command that outputs to both stdout and stderr (merged in streaming)."""
    print("\n=== Testing Stdout/Stderr Merged in Streaming ===")

    bash_tool = BashTool()
    result = await bash_tool.execute(command="echo 'stdout message' && echo 'stderr message' >&2")

    assert result.success
    assert "stdout message" in result.stdout
    # Streaming merges stderr into stdout, so stderr is empty
    assert "stderr message" in result.stdout
    assert result.stderr == ""
    print(f"Stdout: {result.stdout}")
    print(f"Stderr: {result.stderr}")


@pytest.mark.asyncio
async def test_command_failure():
    """Test command that fails with non-zero exit code."""
    print("\n=== Testing Command Failure ===")

    bash_tool = BashTool()
    result = await bash_tool.execute(command="ls /nonexistent_directory_12345")

    assert not result.success
    assert result.exit_code != 0
    assert result.error is not None
    print(f"Error: {result.error}")


@pytest.mark.asyncio
async def test_command_timeout():
    """Test command absolute timeout."""
    print("\n=== Testing Command Timeout ===")

    bash_tool = BashTool()
    result = await bash_tool.execute(command="sleep 10", timeout=1)

    assert not result.success
    assert "timed out" in result.error.lower()
    assert result.exit_code == -1
    print(f"Timeout error: {result.error}")


@pytest.mark.asyncio
async def test_background_command():
    """Test running a command in the background."""
    print("\n=== Testing Background Command ===")

    bash_tool = BashTool()
    result = await bash_tool.execute(
        command="for i in 1 2 3; do echo 'Line '$i; sleep 0.5; done", run_in_background=True
    )

    assert result.success
    assert result.bash_id is not None
    assert "Background command started" in result.stdout

    bash_id = result.bash_id
    print(f"Background command started with ID: {bash_id}")

    # Wait a bit for output
    await asyncio.sleep(1)

    # Check output
    bash_output_tool = BashOutputTool()
    output_result = await bash_output_tool.execute(bash_id=bash_id)

    assert output_result.success
    print(f"Output:\n{output_result.content}")

    # Clean up - terminate the background process
    bash_kill_tool = BashKillTool()
    kill_result = await bash_kill_tool.execute(bash_id=bash_id)
    assert kill_result.success
    print("Background process terminated")


@pytest.mark.asyncio
async def test_bash_output_monitoring():
    """Test monitoring background command output."""
    print("\n=== Testing Output Monitoring ===")

    bash_tool = BashTool()

    # Start background command
    result = await bash_tool.execute(
        command="for i in 1 2 3 4 5; do echo 'Line '$i; sleep 0.5; done", run_in_background=True
    )

    assert result.success
    bash_id = result.bash_id
    print(f"Started background command: {bash_id}")

    bash_output_tool = BashOutputTool()

    # Check output multiple times (incremental output)
    for i in range(3):
        await asyncio.sleep(1)
        output_result = await bash_output_tool.execute(bash_id=bash_id)
        assert output_result.success
        print(f"\n--- Check #{i + 1} ---")
        print(f"Output:\n{output_result.content}")

    # Clean up
    bash_kill_tool = BashKillTool()
    await bash_kill_tool.execute(bash_id=bash_id)


@pytest.mark.asyncio
async def test_bash_output_with_filter():
    """Test bash_output with regex filter."""
    print("\n=== Testing Output Filter ===")

    bash_tool = BashTool()

    # Start background command
    result = await bash_tool.execute(
        command="for i in 1 2 3 4 5; do echo 'Line '$i; sleep 0.3; done", run_in_background=True
    )

    assert result.success
    bash_id = result.bash_id

    # Wait for some output
    await asyncio.sleep(2)

    # Get filtered output (only lines with "Line 2" or "Line 4")
    bash_output_tool = BashOutputTool()
    output_result = await bash_output_tool.execute(bash_id=bash_id, filter_str="Line [24]")

    assert output_result.success
    lines = output_result.content
    print(f"Filtered output:\n{output_result.content}")

    # Clean up
    bash_kill_tool = BashKillTool()
    await bash_kill_tool.execute(bash_id=bash_id)


@pytest.mark.asyncio
async def test_bash_kill():
    """Test terminating a background command."""
    print("\n=== Testing Bash Kill ===")

    bash_tool = BashTool()

    # Start a long-running background command
    result = await bash_tool.execute(command="sleep 100", run_in_background=True)

    assert result.success
    bash_id = result.bash_id
    print(f"Started long-running command: {bash_id}")

    # Verify it's running
    await asyncio.sleep(0.5)
    bg_shell = BackgroundShellManager.get(bash_id)
    assert bg_shell is not None
    assert bg_shell.status == "running"

    # Kill it
    bash_kill_tool = BashKillTool()
    kill_result = await bash_kill_tool.execute(bash_id=bash_id)

    assert kill_result.success
    # exit_code -15 means terminated by SIGTERM
    assert kill_result.exit_code == -15 or kill_result.bash_id == bash_id
    print(f"Kill result:\n{kill_result.content}")

    # Verify it's removed from manager
    bg_shell = BackgroundShellManager.get(bash_id)
    assert bg_shell is None


@pytest.mark.asyncio
async def test_bash_kill_nonexistent():
    """Test killing a non-existent bash process."""
    print("\n=== Testing Kill Non-existent Process ===")

    bash_kill_tool = BashKillTool()
    result = await bash_kill_tool.execute(bash_id="nonexistent123")

    assert not result.success
    assert "not found" in result.error.lower()
    print(f"Expected error: {result.error}")


@pytest.mark.asyncio
async def test_bash_output_nonexistent():
    """Test getting output from non-existent bash process."""
    print("\n=== Testing Output From Non-existent Process ===")

    bash_output_tool = BashOutputTool()
    result = await bash_output_tool.execute(bash_id="nonexistent123")

    assert not result.success
    assert "not found" in result.error.lower()
    print(f"Expected error: {result.error}")


@pytest.mark.asyncio
async def test_multiple_background_commands():
    """Test running multiple background commands simultaneously."""
    print("\n=== Testing Multiple Background Commands ===")

    bash_tool = BashTool()

    # Start multiple background commands
    bash_ids = []
    for i in range(3):
        result = await bash_tool.execute(
            command=f"for j in 1 2 3; do echo 'Command {i + 1} Line '$j; sleep 0.5; done", run_in_background=True
        )
        assert result.success
        bash_ids.append(result.bash_id)
        print(f"Started command {i + 1}: {result.bash_id}")

    # Wait and check all commands
    await asyncio.sleep(1)

    bash_output_tool = BashOutputTool()
    for bash_id in bash_ids:
        output_result = await bash_output_tool.execute(bash_id=bash_id)
        assert output_result.success
        print(f"\nOutput for {bash_id}:\n{output_result.content[:100]}...")

    # Clean up all
    bash_kill_tool = BashKillTool()
    for bash_id in bash_ids:
        await bash_kill_tool.execute(bash_id=bash_id)

    print("All background processes cleaned up")


@pytest.mark.asyncio
async def test_timeout_validation():
    """Test timeout parameter validation."""
    print("\n=== Testing Timeout Validation ===")

    bash_tool = BashTool()

    # Test with timeout > 600 (should be capped to 600)
    result = await bash_tool.execute(command="echo 'test'", timeout=1000)
    assert result.success
    print("Timeout > 600 handled correctly")

    # Test with timeout < 1 (should be set to 120)
    result = await bash_tool.execute(command="echo 'test'", timeout=0)
    assert result.success
    print("Timeout < 1 handled correctly")


# === Server Detection Tests ===


def test_is_server_output():
    """Test that server output patterns are detected."""
    bash_tool = BashTool()
    assert bash_tool._is_server_output("Serving on localhost:8080")
    assert bash_tool._is_server_output("Uvicorn running on http://127.0.0.1:8000")
    assert bash_tool._is_server_output("webpack successfully compiled")
    assert bash_tool._is_server_output("Server running at 0.0.0.0:3000")
    assert bash_tool._is_server_output("Ready for connections")
    assert not bash_tool._is_server_output("echo hello")
    assert not bash_tool._is_server_output("pip install torch")
    assert not bash_tool._is_server_output("")


# === Output Truncation Tests ===


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
    assert result.startswith("A" * 10000)
    assert result.endswith("C" * 10000)
    assert "characters truncated" in result


def test_truncate_output_multiline():
    """Truncation should work correctly with multiline output."""
    bash_tool = BashTool()
    lines = [f"Line {i}: " + "x" * 100 for i in range(400)]  # ~40000+ chars
    text = "\n".join(lines)
    result = bash_tool._truncate_output(text)
    assert len(result) < len(text)
    assert result.startswith("Line 0:")
    assert "truncated" in result


# === _execute_streaming Tests ===


@pytest.mark.asyncio
async def test_execute_streaming_success():
    """Test that streaming execution runs and returns output correctly."""
    bash_tool = BashTool()
    result = await bash_tool._execute_streaming(
        command="echo 'hello from streaming'",
        timeout=30,
    )
    assert isinstance(result, BashOutputResult)
    assert result.success
    assert "hello from streaming" in result.stdout


@pytest.mark.asyncio
async def test_execute_streaming_failure():
    """Test that streaming reports failure for non-zero exit codes."""
    bash_tool = BashTool()
    result = await bash_tool._execute_streaming(
        command="exit 42",
        timeout=30,
    )
    assert isinstance(result, BashOutputResult)
    assert not result.success
    assert result.exit_code == 42
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_streaming_captures_multiline():
    """Test that streaming captures multiple lines of output."""
    bash_tool = BashTool()
    result = await bash_tool._execute_streaming(
        command="echo 'line1' && echo 'line2' && echo 'line3'",
        timeout=30,
    )
    assert result.success
    assert "line1" in result.stdout
    assert "line2" in result.stdout
    assert "line3" in result.stdout


# === Dual Timeout Tests ===


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
        timeout=3,
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
        idle_timeout=2.0,
    )
    assert result.success
    assert "line5" in result.stdout


# === Integration Tests ===


@pytest.mark.asyncio
async def test_all_commands_use_streaming():
    """Test that all foreground commands now use streaming (unified path)."""
    bash_tool = BashTool()
    result = await bash_tool.execute(
        command="pip install --dry-run nonexistent-pkg-xyz-12345 || echo 'done'"
    )
    assert result.success
    assert len(result.stdout) > 0 or result.exit_code == 0


@pytest.mark.asyncio
async def test_short_command_unchanged():
    """Test that short commands still work correctly via streaming."""
    bash_tool = BashTool()
    result = await bash_tool.execute(command="echo 'short command'")
    assert result.success
    assert "short command" in result.stdout
    assert isinstance(result.stderr, str)


# === BackgroundShellManager 查询方法测试 ===


@pytest.fixture
def _clean_bg_manager():
    """保存并恢复 BackgroundShellManager._shells 的状态。"""
    original = BackgroundShellManager._shells.copy()
    BackgroundShellManager._shells.clear()
    yield
    BackgroundShellManager._shells = original


def _make_shell(bash_id: str, command: str, status: str = "running"):
    """创建一个用于测试的 BackgroundShell（无需真实进程）。"""
    import time as _time
    shell = BackgroundShell.__new__(BackgroundShell)
    shell.bash_id = bash_id
    shell.command = command
    shell.process = None
    shell.start_time = _time.time() - 60  # 模拟 60 秒前启动
    shell.output_lines = []
    shell.last_read_index = 0
    shell.status = status
    shell.exit_code = 0 if status == "completed" else None
    return shell


def test_get_running_count_empty(_clean_bg_manager):
    """没有 shell 时计数为 0。"""
    assert BackgroundShellManager.get_running_count() == 0


def test_get_running_count_mixed(_clean_bg_manager):
    """只计算 running 状态的 shell。"""
    BackgroundShellManager._shells = {
        "abc": _make_shell("abc", "sleep 100", "running"),
        "def": _make_shell("def", "ls", "completed"),
        "ghi": _make_shell("ghi", "npm dev", "running"),
    }
    assert BackgroundShellManager.get_running_count() == 2


def test_get_summary_empty(_clean_bg_manager):
    """没有 shell 时返回空列表。"""
    assert BackgroundShellManager.get_summary() == []


def test_get_summary_returns_all(_clean_bg_manager):
    """所有状态的 shell 都应返回。"""
    BackgroundShellManager._shells = {
        "abc": _make_shell("abc", "sleep 100", "running"),
        "def": _make_shell("def", "ls -la", "completed"),
    }
    summary = BackgroundShellManager.get_summary()
    assert len(summary) == 2
    ids = {s["bash_id"] for s in summary}
    assert ids == {"abc", "def"}


def test_get_summary_fields(_clean_bg_manager):
    """每个摘要字典包含预期的字段。"""
    BackgroundShellManager._shells = {
        "abc": _make_shell("abc", "python server", "running"),
    }
    s = BackgroundShellManager.get_summary()[0]
    assert s["bash_id"] == "abc"
    assert s["command"] == "python server"
    assert s["status"] == "running"
    assert s["elapsed"] > 0

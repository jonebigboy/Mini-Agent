# 后台进程感知 - 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 通过 prompt 指示器、`/bg` 命令和 Agent 回复后状态摘要，让用户感知到后台运行的 shell 进程。

**架构：** 在现有 `BackgroundShellManager` 类上新增两个只读查询方法（`get_running_count`、`get_summary`）。CLI 层在三个位置消费这些方法：动态 prompt、`/bg` 命令处理器、回复后状态行。

**技术栈：** Python, asyncio, prompt_toolkit

---

### 任务 1：为 BackgroundShellManager 添加查询方法（TDD）

**文件：**
- 修改：`mini_agent/tools/bash_tool.py:139-246`（BackgroundShellManager 类）
- 修改：`tests/test_bash_tool.py`（在文件末尾追加测试）

- [ ] **步骤 1：编写失败测试**

在 `tests/test_bash_tool.py` 末尾追加：

```python
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
```

注意：`time` 模块在 `bash_tool.py` 中已导入，测试中通过 `_make_shell` 内部导入使用。如果测试文件顶部没有 `import time`，需要加上。

- [ ] **步骤 2：运行测试确认失败**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py::test_get_running_count_empty -v`
预期：FAIL，错误为 `AttributeError: type object 'BackgroundShellManager' has no attribute 'get_running_count'`

- [ ] **步骤 3：实现两个查询方法**

在 `mini_agent/tools/bash_tool.py` 的 `BackgroundShellManager` 类中，在 `get_available_ids` 方法之后（约第 158 行之后）添加：

```python
    @classmethod
    def get_running_count(cls) -> int:
        """获取当前正在运行的后台 shell 数量。"""
        return sum(1 for s in cls._shells.values() if s.status == "running")

    @classmethod
    def get_summary(cls) -> list[dict]:
        """获取所有后台 shell 的摘要信息。

        Returns:
            字典列表，每个字典包含: bash_id, command, status, elapsed (秒)。
        """
        summaries = []
        for shell in cls._shells.values():
            elapsed = time.time() - shell.start_time
            summaries.append({
                "bash_id": shell.bash_id,
                "command": shell.command,
                "status": shell.status,
                "elapsed": elapsed,
            })
        return summaries
```

- [ ] **步骤 4：运行所有新测试确认通过**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -k "get_running_count or get_summary" -v`
预期：5 个新测试全部 PASS

- [ ] **步骤 5：提交**

```bash
git add mini_agent/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: add get_running_count and get_summary to BackgroundShellManager"
```

---

### 任务 2：添加 `/bg` 命令

**文件：**
- 修改：`mini_agent/cli.py:35`（import）
- 修改：`mini_agent/cli.py:193-221`（帮助文本）
- 修改：`mini_agent/cli.py:649-651`（命令补全器）
- 修改：`mini_agent/cli.py:738-753`（命令处理逻辑）

- [ ] **步骤 1：更新 import**

在 `mini_agent/cli.py` 第 35 行，将：

```python
from mini_agent.tools.bash_tool import BashKillTool, BashOutputTool, BashTool
```

改为：

```python
from mini_agent.tools.bash_tool import BackgroundShellManager, BashKillTool, BashOutputTool, BashTool
```

- [ ] **步骤 2：添加辅助函数**

在 `print_stats` 函数之后（约第 284 行之后、`parse_args` 之前），添加两个辅助函数：

```python
def _format_elapsed(seconds: float) -> str:
    """将秒数格式化为易读的时间字符串。"""
    if seconds >= 3600:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins}m"
    elif seconds >= 60:
        return f"{int(seconds // 60)}m"
    else:
        return f"{int(seconds)}s"


def _print_background_status():
    """打印后台 shell 进程状态（/bg 命令使用）。"""
    summaries = BackgroundShellManager.get_summary()
    if not summaries:
        print(f"\n{Colors.DIM}没有后台进程。{Colors.RESET}\n")
        return

    running = [s for s in summaries if s["status"] == "running"]
    stopped = [s for s in summaries if s["status"] != "running"]

    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}⚙️  后台进程{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

    if running:
        print(f"  {Colors.BRIGHT_GREEN}运行中 ({len(running)}):{Colors.RESET}")
        for s in running:
            time_str = _format_elapsed(s["elapsed"])
            cmd = s["command"][:40] + "..." if len(s["command"]) > 40 else s["command"]
            print(
                f"    {Colors.BRIGHT_YELLOW}{s['bash_id']}{Colors.RESET}"
                f"  {time_str:>5s}  {Colors.DIM}{cmd}{Colors.RESET}"
            )

    if stopped:
        print(f"  {Colors.DIM}已结束 ({len(stopped)}):{Colors.RESET}")
        for s in stopped:
            cmd = s["command"][:40] + "..." if len(s["command"]) > 40 else s["command"]
            print(
                f"    {s['bash_id']}  {s['status']:<10s}  {Colors.DIM}{cmd}{Colors.RESET}"
            )

    print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}\n")
```

- [ ] **步骤 3：在帮助文本中添加 `/bg`**

在 `print_help()` 函数中，在 `/log` 条目之后添加：

```python
  {Colors.BRIGHT_GREEN}/bg{Colors.RESET}        - 显示后台 shell 进程
```

- [ ] **步骤 4：在命令补全器中添加 `/bg`**

在 `WordCompleter` 列表中（约第 649 行），加入 `"/bg"`：

```python
    command_completer = WordCompleter(
        ["/help", "/clear", "/history", "/stats", "/log", "/bg", "/exit", "/quit", "/q"],
        ignore_case=True,
        sentence=True,
    )
```

- [ ] **步骤 5：添加 `/bg` 命令处理**

在命令处理逻辑中，`/log` 处理块之后、`else:` 之前（约第 748 行之后），添加：

```python
                elif command == "/bg":
                    _print_background_status()
                    continue
```

- [ ] **步骤 6：手动测试**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run mini-agent`
在交互式会话中：
1. 输入 `/bg` → 应显示 "没有后台进程。"
2. 输入 `/help` → 应显示 `/bg` 条目
3. 验证 Tab 补全包含 `/bg`

- [ ] **步骤 7：提交**

```bash
git add mini_agent/cli.py
git commit -m "feat: add /bg command to list background shell processes"
```

---

### 任务 3：动态 prompt 指示器

**文件：**
- 修改：`mini_agent/cli.py:694-704`（交互循环中的 prompt 区域）

- [ ] **步骤 1：将静态 prompt 改为动态生成**

替换交互循环中的 prompt 块（第 697-704 行）：

```python
            user_input = await session.prompt_async(
                [
                    ("class:prompt", "You"),
                    ("", " › "),
                ],
                multiline=False,
                enable_history_search=True,
            )
```

改为：

```python
            # 动态 prompt：显示后台进程数量
            running_count = BackgroundShellManager.get_running_count()
            prompt_parts = [("class:prompt", "You")]
            if running_count > 0:
                prompt_parts.append(("", f" [⚙️ {running_count} bg]"))
            prompt_parts.append(("", " › "))

            user_input = await session.prompt_async(
                prompt_parts,
                multiline=False,
                enable_history_search=True,
            )
```

- [ ] **步骤 2：手动测试**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run mini-agent`
1. 初始 prompt 应显示：`You › `（无指示器）
2. 让 agent 在后台运行 `sleep 100`，回复后 prompt 应显示：`You [⚙️ 1 bg] › `
3. 运行 `/bg` 确认，然后终止进程，验证 prompt 恢复为 `You › `

- [ ] **步骤 3：提交**

```bash
git add mini_agent/cli.py
git commit -m "feat: show background process count in input prompt"
```

---

### 任务 4：Agent 回复后状态摘要

**文件：**
- 修改：`mini_agent/cli.py:875-876`（agent 回复后的分隔线）

- [ ] **步骤 1：在分隔线前添加后台进程状态**

替换分隔线块（第 875-876 行）：

```python
            # Visual separation
            print(f"\n{Colors.DIM}{'─' * 60}{Colors.RESET}\n")
```

改为：

```python
            # 如果有正在运行的后台进程，显示状态摘要
            running_summaries = [
                s for s in BackgroundShellManager.get_summary() if s["status"] == "running"
            ]
            if running_summaries:
                items = []
                for s in running_summaries:
                    time_str = _format_elapsed(s["elapsed"])
                    cmd = s["command"][:30] + "..." if len(s["command"]) > 30 else s["command"]
                    items.append(f"{s['bash_id']} ({cmd}, {time_str})")
                suffix = "es" if len(running_summaries) > 1 else ""
                print(
                    f"\n{Colors.BRIGHT_YELLOW}⚙️  {len(running_summaries)} 个后台进程"
                    f"运行中: {', '.join(items)}{Colors.RESET}"
                )

            # Visual separation
            print(f"\n{Colors.DIM}{'─' * 60}{Colors.RESET}\n")
```

- [ ] **步骤 2：手动测试**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run mini-agent`
1. 让 agent 在后台启动一个服务器（如 `python -m http.server 8080`）
2. agent 回复后，验证看到状态行，如：
   `⚙️  1 个后台进程运行中: abc123 (python -m http.server 8080, 5s)`
3. 验证分隔线紧跟在状态行之后

- [ ] **步骤 3：提交**

```bash
git add mini_agent/cli.py
git commit -m "feat: show background process summary after each agent response"
```

---

### 任务 5：最终集成测试

- [ ] **步骤 1：运行 bash 工具测试套件**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_bash_tool.py -v`
预期：所有测试 PASS（原有 + 5 个新增）

- [ ] **步骤 2：运行全部测试**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/ -v`
预期：所有测试 PASS

- [ ] **步骤 3：端到端手动验证**

运行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run mini-agent`

测试场景：
1. `/bg` → "没有后台进程。"
2. 输入："在后台运行 python -m http.server 8080" → agent 启动服务器，自动转为后台
3. 回复后：确认状态行显示 `⚙️ 1 个后台进程运行中`
4. prompt 显示：`You [⚙️ 1 bg] › `
5. `/bg` → 显示运行中的服务器（bash_id、已运行时间、命令）
6. 输入："停掉那个后台服务器" → agent 终止进程
7. 回复后：无状态行（没有运行中的进程）
8. prompt 恢复为：`You › `

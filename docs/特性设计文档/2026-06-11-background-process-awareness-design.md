# 后台进程感知（Background Process Awareness）

> 日期：2026-06-11
> 状态：已实现
> 分支：fsw

---

## 1. 问题背景

Mini-Agent 支持后台执行 shell 命令（`run_in_background=True`）和自动检测开发服务器并转为后台运行。但后台进程的管理数据（`BackgroundShellManager._shells`）只存在于 LLM 的消息历史中——**用户完全不知道有哪些进程在后台运行**。

### 改动前的用户体验

```
You › 帮我启动一个 HTTP 服务器

Agent › 好的，我来帮你启动...

⚙️ 检测到开发服务器，已转为后台运行 (bash_id=abc123, 耗时 2.1s)

（用户看到的只有 Agent 的文字回复，之后无法感知 abc123 还在运行）

You › （用户不知道有进程在后台）
```

### 核心缺陷

1. **无主动通知**：Agent 回复后，用户不知道后台进程仍然活着
2. **无查看手段**：没有 CLI 命令让用户列出所有后台进程
3. **无状态指示**：输入 prompt 中没有任何后台进程的提示信息

---

## 2. 设计目标

让用户在三个触点感知到后台进程：

| 触点 | 时机 | 信息量 |
|------|------|--------|
| **Prompt 指示器** | 每次输入时 | 数量（轻量） |
| **`/bg` 命令** | 用户主动查看时 | 完整列表（详情） |
| **回复后状态摘要** | Agent 每次执行完毕后 | 一行摘要（提醒） |

---

## 3. 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                      cli.py (展示层)                     │
│                                                         │
│  ┌───────────┐  ┌───────────────┐  ┌────────────────┐  │
│  │ 动态 Prompt │  │  /bg 命令     │  │ 回复后状态摘要  │  │
│  │ [⚙️ N bg]  │  │  完整列表     │  │  一行摘要      │  │
│  └─────┬─────┘  └───────┬───────┘  └───────┬────────┘  │
│        │                │                   │           │
│        │  调用           │  调用              │  调用      │
│        ▼                ▼                   ▼           │
│  ┌─────────────────────────────────────────────────────┐│
│  │          BackgroundShellManager (数据层)             ││
│  │                                                     ││
│  │  get_running_count() → int     # 运行中的数量       ││
│  │  get_summary() → list[dict]    # 所有进程的详情     ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

**设计原则：**
- 展示逻辑只在 `cli.py` 中，不侵入 `agent.py` 和工具执行逻辑
- 数据查询方法只读，不修改 `_shells` 的状态
- 有进程时才展示，无进程时零侵入

---

## 4. 实现细节

### 4.1 数据层：`BackgroundShellManager` 新增查询方法

**文件：** `mini_agent/tools/bash_tool.py:160-181`

在现有的 `BackgroundShellManager` 类中新增两个类方法，插入位置在 `get_available_ids` 之后、`_remove` 之前。

#### `get_running_count()`

```python
@classmethod
def get_running_count(cls) -> int:
    """获取当前正在运行的后台 shell 数量。"""
    return sum(1 for s in cls._shells.values() if s.status == "running")
```

**实现逻辑：**
- 遍历 `_shells` 字典中的所有 `BackgroundShell` 对象
- 检查每个 shell 的 `status` 字段是否为 `"running"`
- 使用生成器表达式 + `sum()` 计数，避免创建中间列表
- 时间复杂度 O(n)，n 为后台进程总数（通常很小，<10）

**使用场景：** 动态 prompt 中决定是否显示 `[⚙️ N bg]`

#### `get_summary()`

```python
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

**实现逻辑：**
- 遍历 `_shells` 字典，为每个 shell 构建摘要字典
- `elapsed` 通过 `time.time() - shell.start_time` 实时计算（不是缓存值）
- 返回所有状态的进程（包括 completed、failed、terminated），由调用方按需过滤
- 不暴露 `process` 对象和 `output_lines`（这些是内部实现细节）

**返回结构：**
```python
[
    {
        "bash_id": "abc123",       # 8 位 UUID
        "command": "python -m http.server 8080",
        "status": "running",       # running | completed | failed | terminated | error
        "elapsed": 45.3,           # 秒，浮点数
    },
    ...
]
```

**使用场景：** `/bg` 命令和回复后状态摘要

---

### 4.2 辅助函数

**文件：** `mini_agent/cli.py:288-331`

在 `print_stats` 函数之后、`parse_args` 函数之前，添加两个辅助函数。

#### `_format_elapsed(seconds: float) -> str`

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
```

**格式化规则：**

| 输入 | 输出 | 场景 |
|------|------|------|
| `30.5` | `"30s"` | 刚启动 |
| `125.0` | `"2m"` | 运行了几分钟 |
| `3661.0` | `"1h1m"` | 长时间运行 |

**设计决策：** 不展示秒的精度（`2m30s`），因为后台进程的运行时长不需要精确到秒。简洁更重要。

#### `_print_background_status()`

```python
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

**渲染逻辑：**

1. **空状态**：`summaries` 为空时输出 "没有后台进程。" 并立即返回
2. **分区展示**：将进程分为 `running`（运行中）和 `stopped`（已结束）两组
3. **运行中**：黄色显示 `bash_id`、右对齐的运行时长、灰色截断命令
4. **已结束**：灰色显示，包含具体状态（completed/failed/terminated）
5. **命令截断**：超过 40 个字符的命令会截断并加 `...`
6. **对齐**：`time_str` 右对齐 5 字符（`>5s`），确保列对齐

**输出示例：**

```
⚙️  后台进程
────────────────────────────────────────────────────────────
  运行中 (2):
    abc123      5s  python -m http.server 8080
    def456      2m  npm run dev
  已结束 (1):
    ghi789  completed  ls -la
────────────────────────────────────────────────────────────
```

---

### 4.3 `/bg` 命令注册

**文件：** `mini_agent/cli.py`，涉及 4 处改动

#### 改动 1：Import（第 35 行）

```python
# 改动前
from mini_agent.tools.bash_tool import BashKillTool, BashOutputTool, BashTool

# 改动后
from mini_agent.tools.bash_tool import BackgroundShellManager, BashKillTool, BashOutputTool, BashTool
```

新增 `BackgroundShellManager` 的导入，用于在 CLI 层调用 `get_running_count()` 和 `get_summary()`。

#### 改动 2：帮助文本（第 202 行）

在 `print_help()` 函数的命令列表中，`/log` 行之后添加：

```python
  {Colors.BRIGHT_GREEN}/bg{Colors.RESET}        - 显示后台 shell 进程
```

#### 改动 3：命令补全器（第 697 行）

在 `WordCompleter` 的关键词列表中加入 `"/bg"`：

```python
command_completer = WordCompleter(
    ["/help", "/clear", "/history", "/stats", "/log", "/bg", "/exit", "/quit", "/q"],
    ignore_case=True,
    sentence=True,
)
```

用户按 Tab 时可以自动补全 `/bg`。

#### 改动 4：命令处理逻辑（第 801-803 行）

在 `/log` 命令处理之后、`else`（Unknown command）之前，添加：

```python
elif command == "/bg":
    _print_background_status()
    continue
```

**处理流程：** 用户输入 `/bg` → 调用 `_print_background_status()` → 打印进程列表 → `continue` 回到输入循环。

---

### 4.4 动态 Prompt 指示器

**文件：** `mini_agent/cli.py:744-755`

替换原来静态的 prompt 定义。

#### 改动前

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

#### 改动后

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

**实现逻辑：**

1. 每次显示 prompt 前，调用 `BackgroundShellManager.get_running_count()` 获取当前运行中的后台进程数量
2. 构建 `prompt_parts` 列表（prompt_toolkit 支持的格式：`(style_class, text)` 元组列表）
3. 只有 `running_count > 0` 时才插入 `[⚙️ N bg]` 部分
4. 无进程时 prompt 和改动前完全一致：`You › `
5. 有进程时显示：`You [⚙️ 2 bg] › `

**性能影响：** `get_running_count()` 是 O(n) 遍历，n 通常 < 10，可忽略不计。

**prompt_toolkit 兼容性：** `prompt_async` 接受 `FormattedText`（即 `list[tuple[str, str]]`），动态构建这个列表是官方推荐的做法。

---

### 4.5 Agent 回复后状态摘要

**文件：** `mini_agent/cli.py:930-947`

在 Agent 执行完毕、打印分隔线之前，插入状态摘要。

#### 改动前

```python
# Visual separation
print(f"\n{Colors.DIM}{'─' * 60}{Colors.RESET}\n")
```

#### 改动后

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

**实现逻辑：**

1. 调用 `get_summary()` 获取所有进程，过滤出 `status == "running"` 的
2. 无运行中进程时**什么都不输出**（零侵入）
3. 有进程时，为每个进程构建摘要字符串：`bash_id (command, elapsed)`
4. 命令截断阈值 30 字符（比 `/bg` 的 40 字符更短，因为这是一行内联摘要）
5. 输出一行黄色警告，紧跟分隔线

**输出示例：**

```
⚙️  1 个后台进程运行中: abc123 (python -m http.server 8080, 5s)
────────────────────────────────────────────────────────
```

```
⚙️  2 个后台进程运行中: abc123 (python -m http.server 8080, 5s), def456 (npm run dev, 2m)
────────────────────────────────────────────────────────
```

**位置选择：** 放在 `finally` 块之后、分隔线之前。这确保即使 Agent 被取消或出错，只要进程还在运行，摘要就会显示。

---

## 5. 测试设计

**文件：** `tests/test_bash_tool.py:446-515`

### 测试基础设施

#### `_clean_bg_manager` fixture

```python
@pytest.fixture
def _clean_bg_manager():
    """保存并恢复 BackgroundShellManager._shells 的状态。"""
    original = BackgroundShellManager._shells.copy()
    BackgroundShellManager._shells.clear()
    yield
    BackgroundShellManager._shells = original
```

**为什么需要：** `BackgroundShellManager._shells` 是类级别的可变状态。测试之间需要隔离，避免其他测试创建的 shell 影响当前测试。fixture 在测试前保存、清空，测试后恢复。

#### `_make_shell` 辅助函数

```python
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
```

**为什么用 `__new__`：** `BackgroundShell.__init__` 需要一个真实的 `asyncio.subprocess.Process` 对象。测试查询方法时不需要进程对象，直接用 `__new__` 跳过 `__init__`，手动设置属性。

### 测试用例

| 测试名 | 验证内容 |
|--------|---------|
| `test_get_running_count_empty` | 空 `_shells` 时返回 0 |
| `test_get_running_count_mixed` | 混合状态时只计 running |
| `test_get_summary_empty` | 空时返回空列表 |
| `test_get_summary_returns_all` | 返回所有状态的进程 |
| `test_get_summary_fields` | 返回的字典包含正确字段和值 |

---

## 6. 改动后的用户体验

### 完整交互流程

```
You › 帮我启动一个 HTTP 服务器

Agent › Thinking...

   🔧 Tool Call: bash
   Arguments: {"command": "python -m http.server 8080", "run_in_background": true}

   🔄 检测到开发服务器，已转为后台运行 (bash_id=abc123, 耗时 2.1s)

   ✓ 好的，我已经在后台启动了 HTTP 服务器...

⚙️  1 个后台进程运行中: abc123 (python -m http.server 8080, 2s)
────────────────────────────────────────────────────────

You [⚙️ 1 bg] › /bg

⚙️  后台进程
────────────────────────────────────────────────────────
  运行中 (1):
    abc123      2s  python -m http.server 8080
────────────────────────────────────────────────────────

You [⚙️ 1 bg] › 停掉那个服务器

Agent › Thinking...

   🔧 Tool Call: bash_kill
   Arguments: {"bash_id": "abc123"}

   ✓ 已终止后台进程...

────────────────────────────────────────────────────────

You › （恢复正常，没有后台进程指示器）
```

### 三层感知对比

| 感知层 | 无进程 | 1 个进程 | 3 个进程 |
|--------|--------|---------|---------|
| Prompt | `You › ` | `You [⚙️ 1 bg] › ` | `You [⚙️ 3 bg] › ` |
| `/bg` | "没有后台进程。" | 列表 1 项 | 列表 3 项 |
| 回复后 | （无输出） | 1 行摘要 | 1 行摘要 |

---

## 7. 文件改动汇总

| 文件 | 改动类型 | 行数 | 说明 |
|------|---------|------|------|
| `mini_agent/tools/bash_tool.py` | 修改 | +23 行 | `get_running_count()` 和 `get_summary()` |
| `mini_agent/cli.py` | 修改 | +57 行 | import、辅助函数、/bg 命令、动态 prompt、回复后摘要 |
| `tests/test_bash_tool.py` | 修改 | +71 行 | 5 个测试 + fixture + 辅助函数 |

**总改动：** ~150 行（3 个文件）
**不改动的文件：** `agent.py`、`schema.py`、其他工具文件

---

## 8. 局限性与未来改进

### 当前局限

1. **进程完成无主动通知**：进程结束后不会主动通知用户，需要用户通过 `/bg` 或 prompt 指示器变化来发现
2. **已完成进程持续积累**：`_shells` 中已结束的进程不会被自动清理，会一直存在于 `/bg` 列表中
3. **LLM 侧无感知增强**：没有给 LLM 提供额外的后台进程状态信息（比如在 system prompt 中注入）

### 可能的改进方向

1. **进程完成通知**：`BackgroundShellManager` 的 monitor task 检测到进程结束时，通过 `asyncio.Event` 通知 CLI 层打印通知
2. **自动清理**：定期清理已结束超过一定时间的进程记录
3. **LLM 可见性**：将后台进程列表注入到 system prompt 或作为工具返回的上下文中

# 统一流式执行 + 双超时 + 服务器自动检测

## 概述

为 Mini-Agent 的 BashTool 实现统一流式执行机制。所有前台命令均通过 `_execute_streaming()` 轮询执行，实时输出进度到终端。同时引入双超时策略（60s 空闲 / 600s 绝对）和开发服务器自动检测（匹配到服务器启动模式时自动转为后台运行）。

## 为什么需要这个功能

Mini-Agent 之前使用 `process.communicate()` 批量获取输出，命令完全结束后才返回结果。对于快速命令（`git status`、`echo`）这没有问题，但对于以下场景：

1. **包安装** — `pip install torch`（~1GB PyTorch）、`npm install` 可能耗时 5-10 分钟
2. **镜像构建** — `docker build`、`cargo build` 等编译型命令
3. **开发服务器** — `uvicorn`、`flask run`、`npm run dev` 等会阻塞前台的长运行进程

用户看到的现象是：按了确认后 CLI 毫无反应，无法判断是卡死了还是在正常执行。默认 120 秒超时还可能杀掉正在正常下载的进程。

### 业界做法对比

| 工具 | 流式输出 | 超时策略 | 后台执行 | 输出截断 |
|------|---------|---------|---------|---------|
| Claude Code | PTY 持久会话 | 2min 默认 / 10min 最大 | `run_in_background` | 无特殊 |
| Cursor | IDE 终端块流式 | IDE 管理 | 持久终端面板 | 面板限制 |
| **Aider** | **`select()` 100ms 轮询流式** | **双超时：60s 空闲 / 600s 绝对** | **自动服务器检测 16 种模式** | **30K 头尾截断** |
| Codex CLI | 缓冲（非流式） | `--timeout` flag | 不支持 | 格式化结果 |
| Warp | GPU 加速实时流式 | 终端级 | 原生多路复用 | 基于块 |

Aider 的方案最成熟，本设计借鉴其核心思路，采用统一流式执行。

## 架构设计

```
                    ┌──────────────────┐
                    │  LLM 调用 bash   │
                    │   tool.execute() │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   execute() 方法  │
                    └────────┬─────────┘
                             │
                 ┌───────────▼────────────┐
                 │ run_in_background=True? │
                 └───┬───────────────┬────┘
                 否  │               │  是
                     │               │
          ┌──────────▼──────────┐  ┌─▼──────────────┐
          │ _execute_streaming() │  │ BackgroundShell │
          │ 统一流式执行          │  │ 后台执行         │
          │ 双超时（60s/600s）    │  │ 后台管理器       │
          │ 服务器自动检测        │  └────────┬────────┘
          │ 30K 输出截断          │           │
          └──────┬──────────────┘           │
                 │                          │
                 │  ┌─ 检测到服务器 ──┐     │
                 │  │ _promote_to_   │     │
                 │  │ background()   │─────┘
                 │  └───────────────┘
                 │
        ┌────────▼─────────┐
        │  BashOutputResult │  ← 返回类型完全一致
        │  Agent/CLI 无感知  │
        └───────────────────┘
```

### 对 LLM 完全透明

`_execute_streaming()` 的方法签名和返回类型与原前台执行完全一致（`BashOutputResult`），Agent 和 CLI 层无需任何改动。LLM 不知道底层用了不同的执行策略。

---

## 详细设计

### 1. 统一流式执行

所有前台命令统一通过 `_execute_streaming()` 执行，不再区分"长命令"和"短命令"。`communicate()` 已被彻底移除。

```python
# execute() 中的路由逻辑（极简）
else:
    # All foreground commands use streaming execution
    return await self._execute_streaming(command, timeout)
```

### 2. 开发服务器检测模式

使用正则表达式匹配开发服务器启动输出：

```python
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

12 条正则覆盖了 IPv4/IPv6 地址端口绑定、常见框架启动消息、webpack 编译完成等场景。匹配到任意一条时，自动将命令转为后台运行。

### 3. 轮询执行机制

```
_execute_streaming() 流程：

1. 设置 PYTHONUNBUFFERED=1（强制 Python 无缓冲输出）
2. 创建子进程（stdout/stderr 合并）
3. while True 循环：
   ├── 每 200ms 尝试读取一行输出
   │   ├── 有输出 → 实时打印到终端（dim 灰色样式）
   │   │            → 检测是否为服务器输出 → 是则转为后台
   │   └── 无输出 → 检查是否需要进度提示
   ├── 空闲超时检查 → 60s 无输出则 kill 进程
   ├── 绝对超时检查 → 达到 timeout 上限则 kill 进程
   ├── 进程已退出 → 收集剩余输出，break
   └── 超过 5s 无新输出 → 打印 "⏳ 仍在执行中... (已用时 Xs)"
4. 截断输出（30K 上限）
5. 返回 BashOutputResult（与原前台执行完全一致）
```

### 4. 输出截断策略

借鉴 Aider 的头尾截断方案：

- 总输出 <= 30,000 字符：不截断
- 总输出 > 30,000 字符：保留前 10,000 + 后 10,000 字符，中间用 `[... truncated ...]` 标记

头 10K 通常包含环境检测、依赖解析结果；尾 10K 通常包含最终的成功/失败消息。中间的逐个包下载日志对 LLM 决策没有价值。

### 5. 双超时策略

| 超时类型 | 默认值 | 说明 |
|---------|--------|------|
| 空闲超时（idle_timeout） | 60s | 连续 60s 无任何输出则 kill |
| 绝对超时（timeout） | 默认 120s，最大 600s | 总运行时间上限 |

- 空闲超时防止"僵尸命令"（如 `sleep 1000`）占用资源
- 绝对超时防止单次命令运行时间过长
- 两个超时独立生效，任一触发即 kill 进程

### 6. 服务器自动转后台

当 `_is_server_output()` 匹配到输出行时，调用 `_promote_to_background()`：

1. 将进程包装为 `BackgroundShell`
2. 将已收集的输出行添加到 shell
3. 注册到 `BackgroundShellManager` 并启动监控
4. 返回包含 `bash_id` 的 `BashOutputResult`

LLM 收到结果后知道服务器在后台运行，可以用 `bash_output` 工具查看输出。

### 7. 用户体验

**包安装场景：**

```
🔧 Tool Call: bash
   Arguments:
   {
     "command": "uv pip install chromadb sentence-transformers"
   }

   Resolving dependencies...
   Downloading chromadb-0.4.22 (350 kB)
   Downloading torch-2.1.0 (800.2 MB)
   ⏳ 仍在执行中... (已用时 15s)
   Installing collected packages: torch, chromadb
   ⏳ 仍在执行中... (已用时 35s)
   Successfully installed torch-2.1.0 chromadb-0.4.22
   ✓ 命令完成 (耗时 42.3s)
```

**开发服务器场景：**

```
🔧 Tool Call: bash
   Arguments:
   {
     "command": "uvicorn main:app --host 0.0.0.0 --port 8000"
   }

   INFO:     Started server process [12345]
   INFO:     Waiting for application startup.
   INFO:     Application startup complete.
   INFO:     Uvicorn running on http://0.0.0.0:8000
   🔄 检测到开发服务器，已转为后台运行 (bash_id=a1b2c3d4, 耗时 2.3s)

✓ Result: Dev server detected and moved to background (bash_id='a1b2c3d4').
```

---

## 代码走读

### 文件结构

所有改动集中在两个文件，Agent、CLI、安全中间件零改动：

```
mini_agent/tools/bash_tool.py    ← 核心实现
tests/test_bash_tool.py          ← 测试（25 个测试用例）
```

### 模块级常量（第 24-46 行）

**服务器检测模式列表（第 26-39 行）** — 12 条正则覆盖 IPv4/IPv6 端口绑定、常见框架启动消息、webpack 编译等。

**ANSI 颜色常量（第 42-46 行）** — 用 `_C_` 前缀的模块常量（而非 `agent.py` 中的 `Colors` 类），保持 `bash_tool.py` 自包含：

```python
_C_RESET = "\033[0m"
_C_DIM = "\033[2m"
_C_BRIGHT_RED = "\033[91m"
_C_BRIGHT_GREEN = "\033[92m"
_C_BRIGHT_YELLOW = "\033[93m"
```

### `_is_server_output()`（第 270-277 行）

```python
def _is_server_output(self, line: str) -> bool:
    if not line:
        return False
    for pattern in SERVER_DETECTION_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return True
    return False
```

空字符串防御 → 遍历 12 条正则 → 命中任意一条返回 `True`。使用 `re.IGNORECASE` 确保大小写不敏感匹配。

### `_truncate_output()`（第 279-290 行）

```python
def _truncate_output(self, text: str, max_chars: int = 30000,
                     head_chars: int = 10000, tail_chars: int = 10000) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:head_chars]
    tail = text[-tail_chars:]
    truncated_count = len(text) - head_chars - tail_chars
    return f"{head}\n\n... [{truncated_count} characters truncated] ...\n\n{tail}"
```

策略来自 Aider：头 10K 保留环境检测/依赖解析，尾 10K 保留成功/失败结果。

### `_execute_streaming()`（第 507-647 行）

核心方法，所有前台命令的统一执行路径：

**环境准备：**

```python
env = dict(os.environ)
env["PYTHONUNBUFFERED"] = "1"
```

`PYTHONUNBUFFERED=1` 强制 Python 每次写操作立即刷新，确保实时输出。

**子进程创建：**

```python
stderr=asyncio.subprocess.STDOUT   # 合并 stderr 到 stdout
```

合并后所有输出在一个流里，用 `readline()` 统一读取。这也是 `result.stderr` 始终为空字符串的原因。

**轮询循环 — 7 个出口：**

| 出口 | 条件 | 行为 |
|------|------|------|
| 绝对超时 | `elapsed >= timeout` | kill 进程，返回超时错误 |
| 空闲超时 | `time - last_output_time >= idle_timeout` | kill 进程，返回空闲超时错误 |
| stdout 关闭 | `readline()` 返回空 bytes | 进程关闭了输出流，break |
| 服务器检测 | `_is_server_output(line) == True` | 转为后台运行 |
| 进程退出 | `process.returncode is not None` | 排空剩余输出，break |
| 正常读行 | `readline()` 有数据 | 存入 output_lines，实时打印 |
| 无数据 | `readline()` 超时（200ms） | 什么都不做，继续循环 |

`poll_interval = 0.2` 秒控制轮询频率——足够快（用户无感），又不会空转 CPU。

**进度提示：** 两个条件同时满足才打印——距上次提示 >= 5s 且距上次收到输出 >= 5s。避免有输出时出现多余提示。

**收尾：** 合并所有行 → 截断 → 打印完成/失败状态 → 返回 `BashOutputResult`。`stderr` 为空（因为合并到了 stdout）。`exit_code or 0` 防御 `None`。

### `_promote_to_background()`（第 292-329 行）

当检测到服务器输出时调用：

```python
async def _promote_to_background(self, process, command, existing_output, start_time):
    bash_id = str(uuid.uuid4())[:8]
    bg_shell = BackgroundShell(bash_id=bash_id, command=command, process=process, start_time=start_time)
    for line in existing_output:
        bg_shell.add_output(line)
    BackgroundShellManager.add(bg_shell)
    await BackgroundShellManager.start_monitor(bash_id)
    # ... 返回包含 bash_id 的 BashOutputResult
```

将前台进程"无缝"转为后台——进程不中断，已收集的输出保留，后续输出由 `BackgroundShellManager` 的 monitor 任务继续收集。

### `execute()` 路由逻辑（第 494-496 行）

前台分支只有一行调用：

```python
else:
    return await self._execute_streaming(command, timeout)
```

对 Agent 层完全透明。

### 执行流程对比

**普通命令 `echo hello`：**

```
execute("echo hello") → _execute_streaming() → 轮询 200ms → 读到输出 → 打印 → 进程退出 → 返回结果
```

**包安装 `uv pip install chromadb`：**

```
execute("uv pip install chromadb") → _execute_streaming()
  → PYTHONUNBUFFERED=1 → 轮询循环
  → 每行实时打印 → 5s 无输出显示进度提示 → 完成后截断 → 返回 BashOutputResult
```

**开发服务器 `uvicorn main:app`：**

```
execute("uvicorn main:app") → _execute_streaming()
  → 轮询循环 → 读到 "Uvicorn running on..."
  → _is_server_output() → True
  → _promote_to_background() → 注册到 BackgroundShellManager
  → 返回 BashOutputResult(bash_id="xxx")
```

### 测试覆盖矩阵

| 功能 | 测试用例 |
|------|---------|
| 服务器输出检测 | `test_is_server_output` |
| 短文本不截断 | `test_truncate_output_short` |
| 精确阈值不截断 | `test_truncate_output_exact_limit` |
| 超阈值头尾截断 | `test_truncate_output_over_limit` |
| 多行文本截断 | `test_truncate_output_multiline` |
| 流式执行成功 | `test_execute_streaming_success` |
| 流式执行失败 | `test_execute_streaming_failure` |
| 流式多行捕获 | `test_execute_streaming_captures_multiline` |
| 空闲超时 | `test_streaming_idle_timeout` |
| 绝对超时 | `test_streaming_absolute_timeout` |
| 活跃命令存活 | `test_streaming_active_command_survives` |
| 统一路径验证 | `test_all_commands_use_streaming` |
| 短命令不受影响 | `test_short_command_unchanged` |
| 前台命令执行 | `test_foreground_command` |
| stderr 合并 | `test_foreground_command_with_stderr` |
| 命令失败 | `test_command_failure` |
| 绝对超时（execute 入口） | `test_command_timeout` |
| 后台命令 | `test_background_command` |
| 后台输出监控 | `test_bash_output_monitoring` |
| 输出过滤 | `test_bash_output_with_filter` |
| 终止后台命令 | `test_bash_kill` |
| 终止不存在的命令 | `test_bash_kill_nonexistent` |
| 查询不存在的命令 | `test_bash_output_nonexistent` |
| 多命令并发 | `test_multiple_background_commands` |
| 超时验证 | `test_timeout_validation` |

25 个测试 + 174 个其他测试 = **199 个测试全部通过**（3 个 MCP 跳过）。

---

## 改动范围

| 文件 | 改动 |
|------|------|
| `mini_agent/tools/bash_tool.py` | 移除 `LONG_COMMAND_PATTERNS`、`_is_long_command()`；新增 `SERVER_DETECTION_PATTERNS`、`_is_server_output()`、`_promote_to_background()`；重命名 `_execute_long_command` → `_execute_streaming`，添加 `idle_timeout` 参数和空闲超时检查；`execute()` 的 else 分支替换为统一 `_execute_streaming` 调用 |
| `tests/test_bash_tool.py` | 移除 `_is_long_command` / `_execute_long_command` 相关测试，新增服务器检测、双超时、`_execute_streaming` 测试 |

**不改动**：Agent 类、CLI 层、BackgroundShell / BackgroundShellManager、BashOutputTool、BashKillTool。

## 后续升级方向

1. **PTY 支持** — 使用伪终端获得完整的终端模拟体验（支持颜色、交互式命令）
2. **更智能的服务器检测** — 基于进程行为（如打开监听端口）而非仅靠输出匹配
3. **输出进度解析** — 识别 `pip install` 的进度条、`docker pull` 的下载百分比

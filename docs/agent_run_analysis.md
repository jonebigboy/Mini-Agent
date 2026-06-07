# `Agent.run()` 函数深度解析

## 函数签名

```python
async def run(self, cancel_event: Optional[asyncio.Event] = None) -> str:
    """Execute agent loop until task is complete or max steps reached.

    Args:
        cancel_event: Optional asyncio.Event that can be set to cancel execution.
                      When set, the agent will stop at the next safe checkpoint
                      (after completing the current step to keep messages consistent).

    Returns:
        The final response content, or error message (including cancellation message).
    """
```

**关键特点：**
- **异步函数** - 使用 `async def`，内部调用异步 LLM API 和工具执行
- **安全取消** - 通过 `asyncio.Event` 实现协作式取消，在安全检查点停止
- **有限步数** - 受 `max_steps` 约束的循环，防止无限执行
- **自动摘要** - Token 超限时自动压缩历史消息

**源码位置：** `mini_agent/agent.py:321-519`（约 200 行）

---

## 核心数据结构

在深入流程之前，先了解 `run()` 操纵的核心数据模型：

### Message（消息）

```python
class Message(BaseModel):
    role: str                              # "system" | "user" | "assistant" | "tool"
    content: str | list[dict]              # 文本或内容块列表
    thinking: str | None = None            # 扩展思维内容（仅 assistant）
    tool_calls: list[ToolCall] | None = None  # 工具调用（仅 assistant）
    tool_call_id: str | None = None        # 工具调用 ID（仅 tool）
    name: str | None = None                # 工具名称（仅 tool）
```

### ToolResult（工具执行结果）

```python
class ToolResult(BaseModel):
    success: bool
    content: str = ""
    error: str | None = None
```

### 消息历史结构

```
self.messages = [
    Message(role="system", content="..."),        # 系统提示词（永不删除）
    Message(role="user", content="用户任务"),       # 用户输入
    Message(role="assistant", content="...", tool_calls=[...]),  # LLM 响应 + 工具调用
    Message(role="tool", content="...", tool_call_id="..."),     # 工具执行结果
    Message(role="assistant", content="最终回复"),                # 最终回答（无 tool_calls）
]
```

---

## 执行流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent.run() 开始                          │
│  - 设置 cancel_event                                         │
│  - 初始化日志文件                                             │
│  - step = 0, run_start_time = perf_counter()                │
└────────────────────────┬────────────────────────────────────┘
                         ↓
              ┌─────────────────────┐
              │  step < max_steps?  │──No──→ 返回 "max steps exceeded"
              └──────────┬──────────┘
                    Yes  ↓
┌─────────────────────────────────────────────────────────────┐
│ 🛑 取消检查 #1（步骤起始）                                    │
│   if _check_cancelled():                                     │
│       _cleanup_incomplete_messages()                         │
│       return "Task cancelled by user."                       │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 📊 Token 检查 & 消息摘要                                     │
│   await _summarize_messages()                                │
│   - 本地估算 + API 报告的 token 数                           │
│   - 超过 token_limit 时触发摘要                              │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 🖨️ 打印步骤头                                                │
│   ╭──────────────────────────────────────────╮              │
│   │ 💭 Step {step+1}/{max_steps}              │              │
│   ╰──────────────────────────────────────────╯              │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 🤖 LLM 调用                                                 │
│   response = await self.llm.generate(                       │
│       messages=self.messages, tools=tool_list               │
│   )                                                          │
│   - 异常处理（RetryExhaustedError / 其他异常）               │
│   - 更新 api_total_tokens                                   │
│   - 记录请求和响应日志                                       │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 📝 处理响应                                                  │
│   1. 创建 assistant Message 并追加到 messages                │
│   2. 打印 thinking（如果有）                                 │
│   3. 打印 assistant 回复（如果有）                            │
└────────────────────────┬────────────────────────────────────┘
                         ↓
              ┌───────────────────────┐
              │ 有 tool_calls 吗？     │──No──→ 返回 response.content ✅
              └──────────┬────────────┘     （任务完成）
                    Yes  ↓
┌─────────────────────────────────────────────────────────────┐
│ 🛑 取消检查 #2（工具执行前）                                  │
│   if _check_cancelled():                                     │
│       _cleanup_incomplete_messages()                         │
│       return "Task cancelled by user."                       │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 🔧 工具执行循环                                              │
│   for tool_call in response.tool_calls:                      │
│     1. 解析 function_name 和 arguments                       │
│     2. 查找工具（未知工具 → ToolResult.error）               │
│     3. await tool.execute(**arguments)                       │
│     4. 异常捕获 → ToolResult(success=False)                  │
│     5. 打印结果（成功/失败）                                  │
│     6. 追加 tool Message 到 messages                         │
│     7. 🛑 取消检查 #3（每个工具执行后）                       │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ ⏱️ 打印步骤耗时                                              │
│   step += 1                                                  │
└────────────────────────┬────────────────────────────────────┘
                         ↓
                    回到循环起始 ↺
```

---

## 阶段详解

### 阶段 0：初始化（Lines 332-341）

```python
# 1. 设置取消事件（支持外部传入或 self.cancel_event 预设）
if cancel_event is not None:
    self.cancel_event = cancel_event

# 2. 开始新的运行日志
self.logger.start_new_run()
print(f"📝 Log file: {self.logger.get_log_file_path()}")

# 3. 初始化计时器
step = 0
run_start_time = perf_counter()
```

**关键点：**
- `cancel_event` 是协作式取消的基础，由外部（如 CLI 的 Esc 键监听线程）设置
- 使用 `perf_counter()` 而非 `time.time()` 进行高精度计时
- 日志文件按运行实例隔离

---

### 阶段 1：取消检查 #1 — 步骤起始（Lines 344-349）

```python
if self._check_cancelled():
    self._cleanup_incomplete_messages()
    cancel_msg = "Task cancelled by user."
    return cancel_msg
```

```python
# _check_cancelled() 实现
def _check_cancelled(self) -> bool:
    if self.cancel_event is not None and self.cancel_event.is_set():
        return True
    return False
```

```python
# _cleanup_incomplete_messages() 实现
def _cleanup_incomplete_messages(self):
    # 从后往前找到最后一个 assistant 消息
    last_assistant_idx = -1
    for i in range(len(self.messages) - 1, -1, -1):
        if self.messages[i].role == "assistant":
            last_assistant_idx = i
            break
    # 删除该 assistant 消息及其后所有 tool 消息
    if last_assistant_idx != -1:
        removed_count = len(self.messages) - last_assistant_idx
        self.messages = self.messages[:last_assistant_idx]
```

**取消检查点分布（共 3 个）：**
| 检查点 | 位置 | 目的 |
|--------|------|------|
| #1 | 步骤起始 | 在 LLM 调用前停止，避免浪费 API 调用 |
| #2 | 工具执行前 | LLM 已响应但尚未执行工具 |
| #3 | 每个工具执行后 | 保证至少完成一个工具调用 |

**消息清理策略：**
- 删除最后一个 `assistant` 消息 + 其后所有 `tool` 消息
- 保留之前已完成步骤的所有消息
- 确保消息历史的一致性（不出现孤立的 tool 消息）

---

### 阶段 2：Token 管理 & 消息摘要（Lines 353）

```python
await self._summarize_messages()
```

这是 `run()` 中最复杂的辅助方法。详细解析见 [Token 管理与消息摘要](#token-管理与消息摘要) 章节。

---

### 阶段 3：步骤头显示（Lines 355-363）

```python
BOX_WIDTH = 58
step_text = f"{Colors.BOLD}{Colors.BRIGHT_CYAN}💭 Step {step + 1}/{self.max_steps}{Colors.RESET}"
step_display_width = calculate_display_width(step_text)
padding = max(0, BOX_WIDTH - 1 - step_display_width)

print(f"\n{Colors.DIM}╭{'─' * BOX_WIDTH}╮{Colors.RESET}")
print(f"{Colors.DIM}│{Colors.RESET} {step_text}{' ' * padding}{Colors.DIM}│{Colors.RESET}")
print(f"{Colors.DIM}╰{'─' * BOX_WIDTH}╯{Colors.RESET}")
```

**终端输出效果：**
```
╭──────────────────────────────────────────────────────────╮
│ 💭 Step 3/50                                              │
╰──────────────────────────────────────────────────────────╯
```

**技术细节：**
- `calculate_display_width()` 处理 ANSI 转义序列不占显示宽度的问题
- 步骤从 1 开始显示（`step + 1`），更符合用户直觉

---

### 阶段 4：LLM 调用（Lines 365-383）

```python
tool_list = list(self.tools.values())

# 记录请求日志
self.logger.log_request(messages=self.messages, tools=tool_list)

try:
    response = await self.llm.generate(messages=self.messages, tools=tool_list)
except Exception as e:
    from .retry import RetryExhaustedError

    if isinstance(e, RetryExhaustedError):
        error_msg = f"LLM call failed after {e.attempts} retries\nLast error: {str(e.last_exception)}"
    else:
        error_msg = f"LLM call failed: {str(e)}"
    return error_msg
```

**错误处理策略：**

| 异常类型 | 处理方式 | 信息 |
|---------|---------|------|
| `RetryExhaustedError` | 返回重试详情 | 显示重试次数和最后一次错误 |
| 其他 `Exception` | 返回错误消息 | 直接显示异常信息 |

**关键点：**
- LLM 调用失败会**立即终止整个 run**，不继续循环
- 重试逻辑封装在 `LLMClient` 内部（指数退避），`run()` 只处理最终失败

---

### 阶段 5：响应处理（Lines 385-414）

```python
# 1. 累计 API 报告的 token 使用量
if response.usage:
    self.api_total_tokens = response.usage.total_tokens

# 2. 记录响应日志
self.logger.log_response(
    content=response.content,
    thinking=response.thinking,
    tool_calls=response.tool_calls,
    finish_reason=response.finish_reason,
)

# 3. 创建并追加 assistant 消息
assistant_msg = Message(
    role="assistant",
    content=response.content,
    thinking=response.thinking,
    tool_calls=response.tool_calls,
)
self.messages.append(assistant_msg)

# 4. 显示 thinking（如果存在）
if response.thinking:
    print(f"\n🧠 Thinking:")
    print(f"{response.thinking}")

# 5. 显示 assistant 回复
if response.content:
    print(f"\n🤖 Assistant:")
    print(f"{response.content}")
```

**消息结构对应：**

```
LLM 返回的 response 被转化为 Message：
┌─────────────────────────────────────────────────┐
│ Message(role="assistant")                        │
│   content: "我来帮你创建这个文件"                 │
│   thinking: "用户要求创建文件，我需要..."          │  ← 可选
│   tool_calls: [ToolCall(...)]                    │  ← 可选
└─────────────────────────────────────────────────┘
```

---

### 阶段 6：完成判断（Lines 416-421）

```python
if not response.tool_calls:
    step_elapsed = perf_counter() - step_start_time
    total_elapsed = perf_counter() - run_start_time
    print(f"⏱️  Step {step + 1} completed in {step_elapsed:.2f}s (total: {total_elapsed:.2f}s)")
    return response.content
```

**判断逻辑：**
- LLM 不返回 `tool_calls` → 认为任务完成 → 返回文本内容
- LLM 返回 `tool_calls` → 需要继续执行 → 进入工具执行阶段

**退出条件汇总：**
| 退出原因 | 返回值 | 触发位置 |
|---------|--------|---------|
| 任务完成 | `response.content` | 无 tool_calls |
| 用户取消 | `"Task cancelled by user."` | 3 个检查点 |
| LLM 错误 | 错误消息字符串 | LLM 调用异常 |
| 步数耗尽 | `"Task couldn't be completed..."` | 循环结束 |

---

### 阶段 7：工具执行循环（Lines 430-508）

这是 `run()` 的核心执行阶段，每个步骤可能执行多个工具调用。

```python
for tool_call in response.tool_calls:
    tool_call_id = tool_call.id
    function_name = tool_call.function.name
    arguments = tool_call.function.arguments
```

#### 7a. 参数显示

```python
# 截断过长参数值（>200字符），避免终端输出爆炸
truncated_args = {}
for key, value in arguments.items():
    value_str = str(value)
    if len(value_str) > 200:
        truncated_args[key] = value_str[:200] + "..."
    else:
        truncated_args[key] = value
```

#### 7b. 工具查找与执行

```python
if function_name not in self.tools:
    # 未知工具 → 构造失败结果
    result = ToolResult(
        success=False,
        content="",
        error=f"Unknown tool: {function_name}",
    )
else:
    try:
        tool = self.tools[function_name]
        result = await tool.execute(**arguments)
    except Exception as e:
        # 捕获所有异常，转为 ToolResult
        error_detail = f"{type(e).__name__}: {str(e)}"
        error_trace = traceback.format_exc()
        result = ToolResult(
            success=False,
            content="",
            error=f"Tool execution failed: {error_detail}\n\nTraceback:\n{error_trace}",
        )
```

**防御性设计：**
- 未知工具名 → 不会崩溃，返回错误 ToolResult
- 工具执行异常 → 完整捕获 traceback，转为 ToolResult
- **永远不会有未处理的异常** 从工具执行中逃逸

#### 7c. 结果处理

```python
# 日志记录
self.logger.log_tool_result(
    tool_name=function_name,
    arguments=arguments,
    result_success=result.success,
    result_content=result.content if result.success else None,
    result_error=result.error if not result.success else None,
)

# 终端输出（成功结果截断到 300 字符）
if result.success:
    result_text = result.content
    if len(result_text) > 300:
        result_text = result_text[:300] + "..."
    print(f"✓ Result: {result_text}")
else:
    print(f"✗ Error: {result.error}")

# 追加 tool 消息到历史
tool_msg = Message(
    role="tool",
    content=result.content if result.success else f"Error: {result.error}",
    tool_call_id=tool_call_id,
    name=function_name,
)
self.messages.append(tool_msg)
```

**tool 消息结构：**
```
┌──────────────────────────────────────┐
│ Message(role="tool")                  │
│   content: "文件内容..." 或 "Error:..."│
│   tool_call_id: "call_abc123"         │  ← 关联到 assistant 的 tool_call
│   name: "read_file"                   │  ← 工具名称
└──────────────────────────────────────┘
```

#### 7d. 取消检查 #3

```python
# 每个工具执行后都检查取消
if self._check_cancelled():
    self._cleanup_incomplete_messages()
    cancel_msg = "Task cancelled by user."
    return cancel_msg
```

这意味着在一个步骤有多个工具调用时，已执行的工具结果会保留（在被清理前），但未执行的会被跳过。

---

### 阶段 8：步骤结束（Lines 510-514）

```python
step_elapsed = perf_counter() - step_start_time
total_elapsed = perf_counter() - run_start_time
print(f"⏱️  Step {step + 1} completed in {step_elapsed:.2f}s (total: {total_elapsed:.2f}s)")

step += 1
```

---

### 阶段 9：最大步数保护（Lines 516-519）

```python
# while 循环正常退出（step >= max_steps）
error_msg = f"Task couldn't be completed after {self.max_steps} steps."
print(f"⚠️  {error_msg}")
return error_msg
```

---

## Token 管理与消息摘要

### 触发条件

```python
async def _summarize_messages(self):
    if self._skip_next_token_check:
        self._skip_next_token_check = False
        return

    estimated_tokens = self._estimate_tokens()

    # 双重检查：本地估算 OR API 报告
    should_summarize = (
        estimated_tokens > self.token_limit or
        self.api_total_tokens > self.token_limit
    )
```

**为什么双重检查？**
- 本地估算（tiktoken）：快速但可能不准确
- API 报告（`response.usage.total_tokens`）：准确但有延迟（只在上次 LLM 调用后更新）
- 取两者中的"超过"者，更安全

### Token 估算方法

```python
def _estimate_tokens(self) -> int:
    encoding = tiktoken.get_encoding("cl100k_base")  # GPT-4/Claude 兼容

    for msg in self.messages:
        # 文本内容
        if isinstance(msg.content, str):
            total_tokens += len(encoding.encode(msg.content))
        elif isinstance(msg.content, list):
            for block in msg.content:
                total_tokens += len(encoding.encode(str(block)))

        # 思维内容
        if msg.thinking:
            total_tokens += len(encoding.encode(msg.thinking))

        # 工具调用
        if msg.tool_calls:
            total_tokens += len(encoding.encode(str(msg.tool_calls)))

        total_tokens += 4  # 每条消息的元数据开销
```

### 摘要策略

```
摘要前：
  system → user₁ → assistant₁ → tool₁ → assistant₂ → user₂ → assistant₃ → tool₃

摘要后：
  system → user₁ → [summary₁] → user₂ → [summary₂]
```

**核心规则：**
1. **保留所有 user 消息** — 用户意图不可丢失
2. **保留 system 消息** — 系统提示词不变
3. **每轮执行过程（assistant + tool 消息）压缩为摘要**
4. 摘要由 LLM 生成，聚焦于"完成了什么任务"和"调用了什么工具"

### 摘要生成过程

```python
async def _create_summary(self, messages, round_num):
    # 1. 构建原始摘要文本
    summary_content = f"Round {round_num} execution process:\n\n"
    for msg in messages:
        if msg.role == "assistant":
            summary_content += f"Assistant: {content}\n"
            if msg.tool_calls:
                summary_content += f"  → Called tools: {tool_names}\n"
        elif msg.role == "tool":
            summary_content += f"  ← Tool returned: {result_preview}...\n"

    # 2. 调用 LLM 生成精简摘要（独立请求，不使用 agent 的消息历史）
    response = await self.llm.generate(
        messages=[
            Message(role="system", content="You are a summarization assistant."),
            Message(role="user", content=summary_prompt),
        ]
    )
    return response.content
```

**防抖机制：**
```python
self._skip_next_token_check = True  # 摘要后跳过下次检查
```
- 摘要刚完成时，`api_total_tokens` 还是旧值（很大）
- 如果立即检查会再次触发摘要
- 跳过一次，等下次 LLM 调用更新 `api_total_tokens` 后再判断

---

## 消息历史生命周期

```
┌─────────────── run() 开始前 ────────────────┐
│                                              │
│  messages = [                                │
│    system("You are Mini-Agent...")           │
│  ]                                           │
│                                              │
│  cli.py 调用: agent.add_user_message(task)   │
│                                              │
│  messages = [                                │
│    system("You are Mini-Agent..."),          │
│    user("帮我创建一个 hello.txt")             │
│  ]                                           │
└──────────────────────────────────────────────┘
                    ↓
┌─────────────── run() Step 1 ────────────────┐
│                                              │
│  → LLM 调用                                  │
│  → 返回: content="好的", tool_calls=[...]     │
│                                              │
│  messages = [                                │
│    system, user,                             │
│    assistant(content, tool_calls=[write])    │
│  ]                                           │
│                                              │
│  → 执行 write_file 工具                       │
│                                              │
│  messages = [                                │
│    system, user,                             │
│    assistant(content, tool_calls=[write]),   │
│    tool(content="文件已创建", tool_call_id=...) │
│  ]                                           │
└──────────────────────────────────────────────┘
                    ↓
┌─────────────── run() Step 2 ────────────────┐
│                                              │
│  → LLM 调用（带完整历史）                     │
│  → 返回: content="已为你创建了 hello.txt",    │
│          tool_calls=None                     │
│                                              │
│  messages = [                                │
│    system, user,                             │
│    assistant(write_call), tool(write_result), │
│    assistant("已为你创建了...", tool_calls=None)│
│  ]                                           │
│                                              │
│  → 无 tool_calls → return content ✅         │
└──────────────────────────────────────────────┘
```

---

## 取消机制详解

### 协作式取消模型

```
外部（Esc 键线程）          Agent.run() 内部
    │                           │
    │  cancel_event.set()       │
    ├──────────────────────────→│
    │                           │  在检查点发现 is_set()
    │                           │  → _cleanup_incomplete_messages()
    │                           │  → return "Task cancelled"
    │                           │
```

### 三个检查点的位置和意义

```
while step < max_steps:
    │
    ├── 🛑 检查点 #1（步骤起始）
    │     "还没调 LLM，取消不浪费 API"
    │
    ├── 📊 Token 检查
    ├── 🖨️ 步骤头
    ├── 🤖 LLM 调用  ← 耗时操作
    ├── 📝 响应处理
    │
    ├── 🛑 检查点 #2（工具执行前）
    │     "LLM 已返回，但还没执行工具"
    │
    ├── 🔧 工具 1 执行
    ├── 🛑 检查点 #3（工具 1 后）
    ├── 🔧 工具 2 执行
    ├── 🛑 检查点 #3（工具 2 后）
    ├── ...
    │
    └── step += 1 → 回到循环起始
```

### 消息一致性保证

取消时的 `_cleanup_incomplete_messages()` 确保：

```
取消前:                          清理后:
  system                         system
  user                           user
  assistant₁ (完整步骤)          assistant₁ (完整步骤)
  tool₁                          tool₁
  assistant₂ (当前步骤,不完整)    ← 被删除
  tool₂ (部分结果)               ← 被删除
```

这保证下一次 `run()` 调用时，消息历史是干净的、一致的。

---

## 终端输出示例

```
📝 Log file: ~/.mini-agent/logs/run_20260604_143052.jsonl

╭──────────────────────────────────────────────────────────╮
│ 💭 Step 1/50                                              │
╰──────────────────────────────────────────────────────────╯

🧠 Thinking:
用户要求创建一个 Python 脚本，我需要先确认工作区...

🤖 Assistant:
我来帮你创建一个 Python 脚本。首先让我检查工作区。

🔧 Tool Call: bash
   Arguments:
   {
     "command": "ls -la"
   }
✓ Result: total 8
drwxr-xr-x  2 user  staff  64 Jun  4 14:30 .
drwxr-xr-x  3 user...

⏱️  Step 1 completed in 3.21s (total: 3.21s)

╭──────────────────────────────────────────────────────────╮
│ 💭 Step 2/50                                              │
╰──────────────────────────────────────────────────────────╯

🤖 Assistant:
工作区是空的。我来为你创建一个 Python 脚本。

⏱️  Step 2 completed in 2.15s (total: 5.36s)
```

---

## 关键设计模式

### 1. 协作式取消（Cooperative Cancellation）

```python
# 不使用 asyncio.Task.cancel()（会抛 CancelledError）
# 而是通过 Event 标志位，在安全检查点主动退出
cancel_event = asyncio.Event()
# ... 外部设置 ...
cancel_event.set()
# ... 内部检查 ...
if self.cancel_event.is_set():
    self._cleanup_incomplete_messages()  # 保证一致性
    return "cancelled"
```

**优点：**
- 不依赖异常传播，更可控
- 可以在退出前做清理工作
- 消息历史保持一致

### 2. 防御性工具执行

```python
# 工具执行永远返回 ToolResult，不会逃逸异常
try:
    result = await tool.execute(**arguments)
except Exception as e:
    result = ToolResult(success=False, error=traceback)
```

**意义：** LLM 能从错误中恢复——看到 `ToolResult.error` 后可以调整策略。

### 3. 双重 Token 监控

```python
# 本地估算（快速）
estimated_tokens = self._estimate_tokens()

# API 报告（准确）
api_total_tokens = response.usage.total_tokens

# 任一超限即触发摘要
should_summarize = estimated_tokens > limit or api_total_tokens > limit
```

### 4. 分层摘要

```
原始消息:
  user₁ → assistant₁ → tool₁ → assistant₂ → tool₂ → user₂ → assistant₃ → tool₃

每轮独立摘要:
  user₁ → [summary₁: "调用了 bash 和 write_file 完成文件创建"]
  user₂ → [summary₂: "调用了 bash 运行脚本"]
```

保留用户意图，压缩执行细节。

---

## 与 `run_agent()` 的关系

```
run_agent()（cli.py）          Agent.run()（agent.py）
    │                              │
    ├── 加载配置                    │
    ├── 创建 LLM 客户端             │
    ├── 初始化工具列表              │
    ├── 加载系统 Prompt             │
    ├── 创建 Agent 实例             │
    │                              │
    ├── agent.add_user_message()   │
    │                              │
    ├── await agent.run()  ───────→│  进入执行循环
    │                              │  ├── LLM 调用
    │                              │  ├── 工具执行
    │                              │  ├── Token 管理
    │                              │  └── return content
    │                              │
    ├── 打印统计                    │
    └── 清理资源                    │
```

- `run_agent()` 是**编排层**：负责初始化和生命周期管理
- `Agent.run()` 是**执行层**：负责核心的 LLM-工具循环

---

## 总结

`Agent.run()` 是 Mini-Agent 的**核心执行引擎**，设计要点：

| 特性 | 实现方式 |
|------|---------|
| 循环控制 | `while step < max_steps`，防无限执行 |
| 任务完成判断 | LLM 不返回 `tool_calls` 即视为完成 |
| 取消支持 | 3 个检查点 + `_cleanup_incomplete_messages()` |
| Token 管理 | 双重监控（本地 + API） + 自动摘要 |
| 错误处理 | LLM 错误直接终止；工具错误转为 `ToolResult` |
| 日志记录 | 每个步骤的请求、响应、工具结果全记录 |
| 可观测性 | 步骤头、thinking、工具调用、耗时等终端输出 |

**代码行数：** ~200 行
**核心职责：** LLM-工具循环的完整执行
**设计亮点：** 协作式取消、防御性工具执行、自动上下文压缩

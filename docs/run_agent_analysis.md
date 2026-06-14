# `run_agent()` 函数深度解析

## 函数签名

```python
async def run_agent(workspace_dir: Path, task: str = None):
    """Run Agent in interactive or non-interactive mode.

    Args:
        workspace_dir: Workspace directory path
        task: If provided, execute this task and exit (non-interactive mode)
    """
```

**关键特点：**
- ✅ **异步函数** - 使用 `async def`，内部可以调用其他异步函数
- ✅ **双模式** - 支持交互式和非交互式两种运行模式
- ✅ **完整生命周期** - 从配置加载到资源清理的完整流程

---

## 执行流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    run_agent() 开始                         │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 0️⃣ 会话初始化                                                │
│   session_start = datetime.now()                            │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 1️⃣ 配置加载                                                  │
│   - 查找配置文件（优先级：开发 > 用户 > 包）                  │
│   - 验证配置文件存在                                         │
│   - 解析 YAML 配置                                           │
│   - 错误处理（FileNotFoundError, ValueError）               │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 2️⃣ LLM 客户端初始化                                          │
│   - 创建重试配置                                             │
│   - 定义重试回调函数                                         │
│   - 转换 provider 字符串为枚举                               │
│   - 创建 LLMClient 实例                                      │
│   - 设置重试回调                                             │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 3️⃣ 工具初始化                                                │
│   await initialize_base_tools(config)                       │
│   - Bash 工具（BashOutputTool, BashKillTool）               │
│   - MCP 工具加载（异步）                                     │
│   - Skills 加载器（如果启用）                                │
│   - 返回 (tools, skill_loader)                              │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 4️⃣ 添加工作区工具                                            │
│   add_workspace_tools(tools, config, workspace_dir)         │
│   - ReadTool, WriteTool, EditTool（依赖 workspace_dir）      │
│   - BashTool（以 workspace_dir 为工作目录）                 │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 5️⃣ 加载系统 Prompt                                           │
│   Config.find_config_file("system_prompt.md")               │
│   - 优先级搜索                                               │
│   - 读取 Prompt 内容                                         │
│   - 如果找不到，使用默认 Prompt                              │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 6️⃣ 注入 Skills 元数据                                        │
│   - 获取 Skills 元数据 Prompt                                │
│   - 替换 {SKILLS_METADATA} 占位符                            │
│   - 如果没有 Skills，移除占位符                              │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 7️⃣ 创建 Agent 实例                                           │
│   agent = Agent(                                            │
│       llm_client=llm_client,                                │
│       system_prompt=system_prompt,                         │
│       tools=tools,                                         │
│       max_steps=config.agent.max_steps,                    │
│       workspace_dir=str(workspace_dir),                     │
│   )                                                          │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 8️⃣ 显示欢迎信息（仅交互模式）                                │
│   if not task:                                              │
│       print_banner()                                        │
│       print_session_info(...)                               │
└────────────────────────┬────────────────────────────────────┘
                         ↓
              ┌─────────┴─────────┐
              ↓                   ↓
       ┌─────────────┐     ┌──────────────┐
       │ task 有值？  │     │ task 为 None │
       │ 非交互模式   │     │ 交互模式      │
       └──────┬──────┘     └──────┬───────┘
              ↓                   ↓
┌─────────────────────────┐ ┌─────────────────────────┐
│ 8.5️⃣ 非交互模式执行       │ │ 9️⃣ 设置交互式会话       │
│ - 添加任务消息           │ │ - PromptSession 配置    │
│ - await agent.run()     │ │ - 命令补全              │
│ - 打印统计              │ │ - 快捷键绑定            │
│ - 清理 MCP 连接         │ │ - 历史记录              │
│ - return               │ │ - 样式设置              │
└─────────────────────────┘ └──────────┬──────────────┘
                                     ↓
                          ┌─────────────────────────┐
                          │ 10️⃣ 交互式循环           │
                          │ while True:             │
                          │   - 读取用户输入         │
                          │   - 处理命令 (/exit等)  │
                          │   - 运行 Agent          │
                          │   - Esc 取消支持        │
                          │   - 错误处理            │
                          └──────────┬──────────────┘
                                     ↓
                          ┌─────────────────────────┐
                          │ 11️⃣ 清理资源            │
                          │ await _quiet_cleanup()  │
                          │ - 关闭 MCP 连接         │
                          └─────────────────────────┘
```

---

## 核心阶段详解

### 阶段 1：配置加载（Lines 495-536）

```python
# 1. 查找配置文件（优先级搜索）
config_path = Config.get_default_config_path()

# 2. 检查文件是否存在
if not config_path.exists():
    # 显示友好的错误信息和设置指南
    print("❌ Configuration file not found")
    print("🚀 Quick Setup: curl ... | bash")
    return

# 3. 加载并解析配置
try:
    config = Config.from_yaml(config_path)
except FileNotFoundError:
    return
except ValueError as e:
    # 配置格式错误（如缺少必填字段）
    return
except Exception as e:
    # 其他未知错误
    return
```

**关键点：**
- 🔍 **优先级搜索**：开发目录 → 用户目录 → 包目录
- ✅ **友好的错误提示**：告诉用户如何设置
- 🛡️ **异常处理**：捕获不同类型的错误

---

### 阶段 2：LLM 客户端初始化（Lines 538-572）

```python
# 1. 创建重试配置
retry_config = RetryConfig(
    enabled=config.llm.retry.enabled,
    max_retries=config.llm.retry.max_retries,
    initial_delay=config.llm.retry.initial_delay,
    max_delay=config.llm.retry.max_delay,
    exponential_base=config.llm.retry.exponential_base,
    retryable_exceptions=(Exception,),
)

# 2. 定义重试回调（用于显示重试信息）
def on_retry(exception, attempt):
    print(f"⚠️ LLM call failed (attempt {attempt}): {str(exception)}")
    print(f"   Retrying in {next_delay:.1f}s...")

# 3. 转换 provider 字符串为枚举
provider = LLMProvider.ANTHROPIC if config.llm.provider.lower() == "anthropic" else LLMProvider.OPENAI

# 4. 创建 LLM 客户端
llm_client = LLMClient(
    api_key=config.llm.api_key,
    provider=provider,
    api_base=config.llm.api_base,
    model=config.llm.model,
    retry_config=retry_config if config.llm.retry.enabled else None,
)

# 5. 设置重试回调
if config.llm.retry.enabled:
    llm_client.retry_callback = on_retry
```

**关键点：**
- 🔄 **重试机制**：支持指数退避
- 📊 **重试可视化**：通过回调函数显示重试信息
- 🔧 **Provider 转换**：字符串 → 枚举类型

---

### 阶段 3：工具初始化（Lines 574-578）

```python
# 1️⃣ 初始化基础工具（异步）
tools, skill_loader = await initialize_base_tools(config)

# initialize_base_tools 内部做了什么：
# - BashOutputTool（监控 Bash 输出）
# - BashKillTool（杀死 Bash 进程）
# - MCP 工具（异步加载）
# - Skills 加载器（如果启用）

# 2️⃣ 添加工作区依赖的工具
add_workspace_tools(tools, config, workspace_dir)

# add_workspace_tools 内部做了什么：
# - ReadTool（读取文件，需要 workspace_dir）
# - WriteTool（写入文件，需要 workspace_dir）
# - EditTool（编辑文件，需要 workspace_dir）
# - BashTool（Shell 命令，以 workspace_dir 为 cwd）
```

**为什么分两步？**
- **基础工具**：不依赖工作区，可以从包配置加载
- **工作区工具**：依赖用户指定的 `workspace_dir`

---

### 阶段 4-6：Prompt 处理（Lines 580-601）

```python
# 1️⃣ 加载系统 Prompt（优先级搜索）
system_prompt_path = Config.find_config_file(config.agent.system_prompt_path)
if system_prompt_path and system_prompt_path.exists():
    system_prompt = system_prompt_path.read_text(encoding="utf-8")
else:
    system_prompt = "You are Mini-Agent..."  # 默认 Prompt

# 2️⃣ 注入 Skills 元数据
if skill_loader:
    skills_metadata = skill_loader.get_skills_metadata_prompt()
    if skills_metadata:
        # 替换占位符
        system_prompt = system_prompt.replace(
            "{SKILLS_METADATA}",
            skills_metadata
        )
    else:
        # 移除占位符
        system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")
else:
    # 如果没有启用 Skills，移除占位符
    system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")
```

**Prompt 占位符机制：**
```
原始 system_prompt.md:
"""
You are Mini-Agent...

Available Skills:
{SKILLS_METADATA}
"""

替换后:
"""
You are Mini-Agent...

Available Skills:
- pdf: Create and edit PDF documents
- docx: Create Word documents
- xlsx: Create Excel spreadsheets
...
"""
```

---

### 阶段 7：创建 Agent（Lines 603-610）

```python
agent = Agent(
    llm_client=llm_client,          # LLM 客户端
    system_prompt=system_prompt,    # 系统提示词
    tools=tools,                    # 工具列表
    max_steps=config.agent.max_steps,  # 最大步数
    workspace_dir=str(workspace_dir),  # 工作目录
)
```

**Agent 内部初始化：**
```python
# Agent.__init__ 内部会：
# 1. 保存工具到字典：self.tools = {tool.name: tool}
# 2. 创建消息历史：self.messages = [system_message]
# 3. 注入工作区信息到 system_prompt
# 4. 初始化日志记录器
```

---

### 阶段 8.5：非交互模式（Lines 617-630）

```python
if task:  # 如果提供了 task 参数
    print(f"\nAgent › Executing task...\n")

    # 1. 添加任务消息
    agent.add_user_message(task)

    # 2. 运行 Agent
    try:
        await agent.run()  # 执行 Agent 循环
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        print_stats(agent, session_start)  # 打印统计

    # 3. 清理 MCP 连接
    await _quiet_cleanup()
    return  # 直接返回，不进入交互模式
```

**使用场景：**
```bash
# 执行单个任务后退出
mini-agent --task "创建一个 hello.txt 文件"
mini-agent -t "帮我写一个 Python 脚本"
```

---

### 阶段 9：设置交互式会话（Lines 632-676）

```python
# 1️⃣ 命令补全器
command_completer = WordCompleter(
    ["/help", "/clear", "/history", "/stats", "/log", "/exit", "/quit", "/q"],
    ignore_case=True,
    sentence=True,
)

# 2️⃣ 自定义样式
prompt_style = Style.from_dict({
    "prompt": "#00ff00 bold",  # 绿色加粗
    "separator": "#666666",    # 灰色
})

# 3️⃣ 自定义快捷键
kb = KeyBindings()

@kb.add("c-u")  # Ctrl+U：清空当前行
def _(event):
    event.current_buffer.reset()

@kb.add("c-l")  # Ctrl+L：清屏
def _(event):
    event.app.renderer.clear()

@kb.add("c-j")  # Ctrl+J：插入换行
def _(event):
    event.current_buffer.insert_text("\n")

# 4️⃣ 创建 Prompt 会话（带历史记录）
history_file = Path.home() / ".mini-agent" / ".history"
session = PromptSession(
    history=FileHistory(str(history_file)),
    auto_suggest=AutoSuggestFromHistory(),
    completer=command_completer,
    style=prompt_style,
    key_bindings=kb,
)
```

**prompt_toolkit 功能：**
- ✅ **历史记录**：上下键查看历史命令
- ✅ **自动补全**：Tab 键补全命令
- ✅ **历史搜索**：Ctrl+R 搜索历史
- ✅ **多行输入**：Ctrl+J 换行
- ✅ **自定义快捷键**

---

### 阶段 10：交互式循环（Lines 678-839）

```python
while True:  # 无限循环
    try:
        # 1️⃣ 读取用户输入
        user_input = await session.prompt_async(
            [("class:prompt", "You"), ("", " › ")],
            multiline=False,
            enable_history_search=True,
        )
        user_input = user_input.strip()

        if not user_input:
            continue  # 空输入，跳过

        # 2️⃣ 处理命令（以 / 开头）
        if user_input.startswith("/"):
            command = user_input.lower()

            if command in ["/exit", "/quit", "/q"]:
                # 退出命令
                print_stats(agent, session_start)
                break

            elif command == "/help":
                print_help()
                continue

            elif command == "/clear":
                # 清空消息历史
                agent.messages = [agent.messages[0]]
                continue

            elif command == "/history":
                print(f"Current message count: {len(agent.messages)}")
                continue

            elif command == "/stats":
                print_stats(agent, session_start)
                continue

            elif command == "/log" or command.startswith("/log "):
                # 查看日志
                parts = user_input.split(maxsplit=1)
                if len(parts) == 1:
                    show_log_directory()
                else:
                    read_log_file(parts[1])
                continue

        # 3️⃣ 检查退出命令（不带斜杠）
        if user_input.lower() in ["exit", "quit", "q"]:
            print_stats(agent, session_start)
            break

        # 4️⃣ 运行 Agent
        print(f"\nAgent › Thinking... (Esc to cancel)\n")
        agent.add_user_message(user_input)

        # 5️⃣ 设置取消机制
        cancel_event = asyncio.Event()
        agent.cancel_event = cancel_event

        # 6️⃣ 启动 Esc 键监听线程
        esc_thread = threading.Thread(target=esc_key_listener, daemon=True)
        esc_thread.start()

        # 7️⃣ 运行 Agent（可取消）
        try:
            agent_task = asyncio.create_task(agent.run())

            # 轮询取消状态
            while not agent_task.done():
                if esc_cancelled[0]:
                    cancel_event.set()
                await asyncio.sleep(0.1)

            _ = agent_task.result()

        except asyncio.CancelledError:
            print(f"\n⚠️ Agent execution cancelled")
        finally:
            agent.cancel_event = None
            esc_listener_stop.set()
            esc_thread.join(timeout=0.2)

        print(f"\n{'─' * 60}\n")

    except KeyboardInterrupt:
        # Ctrl+C 处理
        print(f"\n\n👋 Interrupt signal detected, exiting...\n")
        break

    except Exception as e:
        print(f"\n❌ Error: {e}\n")
```

**Esc 取消机制：**
```python
# 1️⃣ 创建取消事件
cancel_event = asyncio.Event()

# 2️⃣ 启动监听线程（监听 Esc 键）
def esc_key_listener():
    while not esc_listener_stop.is_set():
        if 检测到 Esc 键:
            cancel_event.set()  # 设置取消标志
            break

# 3️⃣ Agent 内部定期检查
# agent.run() 内部：
if self._check_cancelled():  # 检查 cancel_event
    self._cleanup_incomplete_messages()
    return "Task cancelled"

# 4️⃣ 轮询检查
while not agent_task.done():
    if esc_cancelled[0]:
        cancel_event.set()
    await asyncio.sleep(0.1)
```

---

### 阶段 11：清理资源（Line 841）

```python
await _quiet_cleanup()

# _quiet_cleanup 内部：
async def _quiet_cleanup():
    """静默清理 MCP 连接"""
    try:
        await cleanup_mcp_connections()
    except Exception:
        pass  # 静默忽略清理错误

# cleanup_mcp_connections 内部：
# - 遍历所有 MCP 会话
# - 关闭每个会话
# - 清理资源
```

---

## 交互式命令总结

| 命令 | 功能 | 实现 |
|------|------|------|
| `/exit` `/quit` `/q` | 退出程序 | `break` 循环 |
| `/help` | 显示帮助 | `print_help()` |
| `/clear` | 清空消息历史 | `agent.messages = [agent.messages[0]]` |
| `/history` | 显示消息数量 | `print(len(agent.messages))` |
| `/stats` | 显示会话统计 | `print_stats(agent, session_start)` |
| `/log` | 显示日志目录 | `show_log_directory()` |
| `/log <file>` | 读取指定日志 | `read_log_file(filename)` |

---

## 关键设计模式

### 1️⃣ **建造者模式**

```python
# 分步骤构建 Agent 对象
agent = Agent()  # 第 1 步：创建
    ↓
agent.add_user_message(task)  # 第 2 步：添加内容
    ↓
await agent.run()  # 第 3 步：运行
```

### 2️⃣ **模板方法模式**

```python
# 两种模式遵循相同流程，但某些步骤不同
run_agent(workspace_dir, task=None)   # 交互模式
run_agent(workspace_dir, task="xxx")  # 非交互模式

# 共同步骤：加载配置 → 初始化工具 → 创建 Agent
# 不同步骤：交互模式有循环，非交互模式直接退出
```

### 3️⃣ **观察者模式**

```python
# 重试回调
llm_client.retry_callback = on_retry

# 当重试发生时，自动调用回调函数
# on_retry(exception, attempt)  # 显示重试信息
```

### 4️⃣ **策略模式**

```python
# 不同的运行策略
if task:
    # 非交互策略
    await agent.run()
    return
else:
    # 交互策略
    while True:
        ...
```

---

## 错误处理策略

### 1️⃣ **分层异常处理**

```python
# 配置加载层
try:
    config = Config.from_yaml(config_path)
except FileNotFoundError:
    # 文件不存在
except ValueError:
    # 配置格式错误
except Exception:
    # 其他未知错误

# Agent 运行层
try:
    await agent.run()
except asyncio.CancelledError:
    # 用户取消
except Exception as e:
    # 运行时错误
```

### 2️⃣ **资源清理保证**

```python
try:
    await agent.run()
except Exception:
    ...
finally:
    # 无论成功失败，都会执行
    print_stats(agent, session_start)
    await _quiet_cleanup()
```

### 3️⃣ **优雅降级**

```python
# 找不到配置文件 → 显示设置指南
# 找不到系统 Prompt → 使用默认 Prompt
# Skills 加载失败 → 移除占位符，继续运行
# MCP 连接失败 → 跳过该 MCP 工具
```

---

## 性能优化点

### 1️⃣ **异步 I/O**

```python
# 所有网络请求都是异步的
await initialize_base_tools(config)  # MCP 加载
await agent.run()                     # LLM API 调用
await _quiet_cleanup()                # 资源清理
```

### 2️⃣ **延迟加载**

```python
# MCP 工具按需加载
if config.tools.enable_mcp:
    mcp_tools = await load_mcp_tools_async(...)
```

### 3️⃣ **会话复用**

```python
# PromptSession 复用，避免重复创建
session = PromptSession(...)
while True:
    user_input = await session.prompt_async(...)  # 复用
```

---

## 总结

`run_agent()` 函数是整个应用的**核心编排者**，负责：

1. ✅ **资源初始化**：配置、LLM、工具、Prompt
2. ✅ **模式切换**：交互式 vs 非交互式
3. ✅ **会话管理**：命令处理、历史记录、快捷键
4. ✅ **错误处理**：分层异常捕获、友好提示
5. ✅ **资源清理**：MCP 连接关闭、日志记录

**代码行数：** ~350 行
**核心职责：** 应用的完整生命周期管理
**设计亮点：** 分层清晰、职责单一、易于扩展

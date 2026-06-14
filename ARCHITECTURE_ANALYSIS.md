# Mini-Agent 完整架构分析

## 1. 项目概述

### 1.1 项目功能与定位

**Mini-Agent** 是一个最小化的 AI Agent 演示项目，展示如何使用 MiniMax M2.5 模型构建智能代理系统。

**核心功能：**
- 🔧 **工具执行循环** - 完整的 Agent 推理-行动-观察循环
- 🧠 **智能对话管理** - 支持长对话的自动摘要和上下文管理
- 🛠️ **工具系统** - 文件操作、Shell 命令、笔记记录等基础工具
- 🔌 **MCP 集成** - Model Context Protocol 支持，可扩展外部工具
- 📚 **Claude Skills** - 集成 Claude 官方技能库（文档处理、设计、测试等）
- 📝 **日志系统** - 完整的请求/响应/工具执行日志记录

**使用场景：**
- AI Agent 开发学习和研究
- 自动化任务执行（文件操作、代码生成、测试等）
- 智能文档处理和分析
- 作为更复杂 Agent 系统的基础框架

### 1.2 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **编程语言** | Python 3.10+ | 异步编程支持 |
| **LLM 接口** | Anthropic API / OpenAI API | 兼容两大主流协议 |
| **模型** | MiniMax M2.5 | 支持扩展思考（interleaved thinking） |
| **依赖管理** | uv | 现代 Python 包管理工具 |
| **配置管理** | YAML + Pydantic | 类型安全的配置 |
| **异步框架** | asyncio | 异步工具执行 |
| **终端 UI** | prompt_toolkit | 交互式命令行界面 |
| **工具协议** | MCP (Model Context Protocol) | 标准化工具接口 |
| **日志** | 文件日志系统 | 按会话记录完整日志 |

---

## 2. 目录结构

```
Mini-Agent/
├── mini_agent/              # 核心源代码
│   ├── __init__.py          # 包导出
│   ├── agent.py             # ⭐ Agent 核心实现
│   ├── cli.py               # ⭐ 命令行入口
│   ├── config.py            # 配置管理
│   ├── logger.py            # 日志系统
│   ├── retry.py             # 重试机制
│   ├── schema/              # 数据模型
│   │   └── schema.py        # Message, LLMResponse 等
│   ├── llm/                 # LLM 客户端
│   │   ├── base.py          # 基础接口
│   │   ├── llm_wrapper.py   # ⭐ 多 Provider 包装器
│   │   ├── anthropic_client.py
│   │   └── openai_client.py
│   ├── tools/               # 工具实现
│   │   ├── base.py          # Tool 基类
│   │   ├── file_tools.py    # 文件操作工具
│   │   ├── bash_tool.py     # Shell 命令工具
│   │   ├── memory_manager.py # 跨会话记忆管理
│   │   ├── mcp_loader.py    # MCP 工具加载器
│   │   ├── skill_loader.py  # Skill 加载器
│   │   └── skill_tool.py    # Skill 调用工具
│   ├── utils/               # 工具函数
│   ├── config/              # 配置文件
│   │   ├── config.yaml      # 主配置
│   │   ├── mcp.json         # MCP 配置
│   │   └── system_prompt.md # 系统 Prompt
│   └── skills/              # Claude Skills (git submodule)
├── tests/                   # 测试代码
│   ├── test_agent.py        # Agent 测试
│   ├── test_mcp.py          # MCP 测试
│   └── test_note_tool.py    # 笔记工具测试
├── docs/                    # 文档
├── examples/                # 示例
├── workspace/               # 默认工作区
├── pyproject.toml           # 项目配置
└── README.md                # 项目说明
```

**目录说明：**
- `mini_agent/` - 核心业务逻辑
- `mini_agent/llm/` - LLM 通信层，支持多 Provider
- `mini_agent/tools/` - 所有工具实现，遵循统一接口
- `mini_agent/config/` - 配置文件，支持多层级查找
- `tests/` - 完整的单元测试和集成测试

---

## 3. 核心模块拆解

### 3.1 模块职责与关系

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI (cli.py)                         │
│  - 命令行参数解析                                             │
│  - 配置加载                                                   │
│  - 工具初始化与组装                                           │
│  - 交互式会话管理                                             │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                     Agent (agent.py)                        │
│  - 消息历史管理                                               │
│  - Token 计数与摘要                                          │
│  - 工具调用循环                                               │
│  - 执行状态追踪                                               │
└────────┬──────────────┬──────────────┬─────────────────────┘
         │              │              │
         ▼              ▼              ▼
    ┌─────────┐   ┌─────────┐   ┌─────────────┐
    │   LLM   │   │  Tools  │   │   Logger    │
    │ Client  │   │ Manager │   │   System    │
    └────┬────┘   └────┬────┘   └─────────────┘
         │             │
         ▼             ▼
    ┌─────────┐   ┌─────────────────────────┐
    │Anthropic│   │ File │ Bash │Memory│ MCP │
    │  OpenAI │   │ Tools│ Tool │ Mgr  │Tools│
    └─────────┘   └─────────────────────────┘
```

### 3.2 核心类/函数说明

#### **Agent 类** (`agent.py`)

```python
class Agent:
    def __init__(
        llm_client: LLMClient,
        system_prompt: str,
        tools: list[Tool],
        max_steps: int = 50,
        workspace_dir: str = "./workspace",
        token_limit: int = 80000,
    )

    async def run(cancel_event: Optional[asyncio.Event]) -> str:
        """主执行循环：思考 → 行动 → 观察"""

    async def _summarize_messages():
        """Token 超限时自动摘要对话历史"""

    def _estimate_tokens() -> int:
        """使用 tiktoken 精确计算 Token 数"""
```

**职责：**
- 维护消息历史和系统 Prompt
- 执行 Agent 循环：获取 LLM 响应 → 执行工具 → 收集结果
- Token 管理：超限时自动摘要
- 取消支持：安全中断执行

#### **LLMClient** (`llm/llm_wrapper.py`)

```python
class LLMClient:
    def __init__(
        api_key: str,
        provider: LLMProvider,  # "anthropic" or "openai"
        api_base: str,
        model: str,
    )

    async def generate(
        messages: list[Message],
        tools: list[Tool] | None = None,
    ) -> LLMResponse:
        """统一的 LLM 调用接口"""
```

**职责：**
- 多 Provider 支持（Anthropic / OpenAI）
- MiniMax API 自动路径处理
- 统一的响应格式
- 重试机制集成

#### **Tool 基类** (`tools/base.py`)

```python
class Tool:
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters(self) -> dict[str, Any]: ...

    async def execute(**kwargs) -> ToolResult: ...
```

**职责：**
- 定义统一的工具接口
- 支持自动转换为 Anthropic/OpenAI 工具 Schema

**具体工具：**
- `ReadTool` / `WriteTool` / `EditTool` - 文件操作
- `BashTool` / `BashKillTool` / `BashOutputTool` - Shell 命令
- `MemoryManager` - 跨会话持久化记忆（Markdown-based）
- `MCPTool` - MCP 工具包装器
- `SkillTool` - Claude Skill 调用

#### **Config** (`config.py`)

```python
class Config(BaseModel):
    llm: LLMConfig        # API 配置
    agent: AgentConfig    # Agent 配置
    tools: ToolsConfig    # 工具开关

    @classmethod
    def load() -> "Config":
        """按优先级加载配置：开发目录 > 用户目录 > 包目录"""
```

**职责：**
- 分层配置管理
- 类型安全的配置验证
- 多路径配置查找

---

## 4. 执行流程

### 4.1 程序入口流程

```
用户执行: mini-agent --workspace /path/to/project
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. CLI 入口 (cli.py::main())                                  │
│    - 解析命令行参数                                            │
│    - 加载配置 (Config.load)                                   │
│    - 初始化 LLM 客户端                                        │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. 工具初始化 (cli.py::initialize_tools())                    │
│    - 文件工具 (Read/Write/Edit)                               │
│    - Bash 工具                                                │
│    - 记忆系统 (MemoryManager)                                │
│    - MCP 工具加载 (load_mcp_tools_async)                      │
│    - Skills 工具加载 (create_skill_tools)                     │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Agent 创建                                                 │
│    agent = Agent(                                             │
│        llm_client=llm,                                       │
│        system_prompt=system_prompt,                          │
│        tools=tools,                                          │
│        max_steps=50,                                         │
│    )                                                          │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. 交互式会话循环 (cli.py::run_interactive())                 │
│    while True:                                               │
│        - 读取用户输入 (prompt_toolkit)                        │
│        - 处理特殊命令 (/exit, /clear, /stats)                 │
│        - agent.add_user_message(user_input)                  │
│        - result = await agent.run()                          │
│        - 显示结果                                             │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Agent 执行流程

```
agent.run() 开始
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step N: 检查取消状态                                          │
│ - 如果 cancel_event.is_set(): 清理消息并返回                  │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Token 管理                                                    │
│ - 计算当前消息历史 Token 数                                   │
│ - 如果超过 limit: 触发 _summarize_messages()                 │
│   * 保留所有用户消息                                         │
│   * 摘要每轮执行过程                                         │
│   * 压缩历史                                                 │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ LLM 推理                                                       │
│ response = await llm.generate(messages, tools)              │
│   * 发送消息历史                                              │
│   * 发送工具列表（转换为 Schema）                             │
│   * 接收响应（content + thinking + tool_calls）              │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ 响应处理                                                      │
│ - 记录 API Token 使用量                                      │
│ - 添加 assistant 消息到历史                                  │
│ - 显示 thinking 内容（如果有）                               │
│ - 显示 response 内容                                         │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
         有 tool_calls？
         │           │
         Yes         No
         │           │
         ▼           ▼
┌──────────────────┐  返回 response.content
│ 执行工具调用      │  (任务完成)
│ for tool_call:   │
│   - 解析工具名    │
│   - 解析参数      │
│   - await execute│
│   - 收集结果      │
│   - 添加 tool msg│
└────────┬─────────┘
         │
         ▼
    进入下一轮 (Step N+1)
```

### 4.3 工具执行流程

```
LLM 决定调用工具: read_file(path="config.yaml")
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. 工具查找                                                   │
│ tool = agent.tools["read_file"]                              │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. 参数解析                                                   │
│ arguments = {"path": "config.yaml"}                          │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. 工具执行                                                   │
│ try:                                                         │
│     result = await tool.execute(**arguments)                │
│     # ToolResult(success=True, content="...", error=None)   │
│ except Exception as e:                                      │
│     result = ToolResult(success=False, error=str(e))        │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. 结果记录                                                   │
│ - logger.log_tool_result(...)                                │
│ - 添加 tool 消息到历史:                                      │
│   Message(                                                   │
│       role="tool",                                          │
│       content=result.content,                               │
│       tool_call_id=...,                                     │
│   )                                                          │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. 返回 LLM                                                   │
│ 带着工具结果，进入下一轮 LLM 推理                              │
└──────────────────────────────────────────────────────────────┘
```

### 4.4 MCP 工具加载流程

```
启动时: load_mcp_tools_async(config.mcp_config_path)
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. 读取 mcp.json                                              │
│ {                                                            │
│   "mcpServers": {                                            │
│     "memory": {                                              │
│       "command": "npx",                                      │
│       "args": ["-y", "@modelcontextprotocol/server-memory"],│
│       "disabled": false                                      │
│     }                                                         │
│   }                                                           │
│ }                                                            │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. 过滤并启动服务器                                           │
│ for server_name, server_config in servers:                  │
│   if server_config.disabled: continue                       │
│                                                              │
│   # stdio 连接                                               │
│   server_params = StdioServerParameters(                    │
│       command=server_config.command,                        │
│       args=server_config.args,                              │
│   )                                                          │
│   stdio_transport = await stdio_client(server_params)       │
│   session = ClientSession(stdio_transport)                  │
│   await session.initialize()                                │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. 列出可用工具                                               │
│ tools_response = await session.list_tools()                │
│ for tool_schema in tools_response.tools:                   │
│   mcp_tool = MCPTool(                                       │
│       name=tool_schema.name,                                │
│       description=tool_schema.description,                  │
│       parameters=tool_schema.inputSchema,                   │
│       session=session,                                      │
│   )                                                          │
│   tools.append(mcp_tool)                                    │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. 返回工具列表                                               │
│ return tools  # 可被 Agent 使用                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. 架构特点

### 5.1 设计模式

#### **1. 策略模式 (Strategy Pattern)**
```python
# LLM Provider 可切换
LLMClient(provider="anthropic")  # Anthropic API
LLMClient(provider="openai")     # OpenAI API
```

#### **2. 工厂模式 (Factory Pattern)**
```python
# Config.load() 根据优先级查找配置
Config.find_config_file("config.yaml")
# 1. 开发目录 → 2. 用户目录 → 3. 包目录
```

#### **3. 包装器模式 (Wrapper Pattern)**
```python
# MCPTool 包装 MCP 会话
class MCPTool(Tool):
    async def execute(**kwargs):
        result = await self._session.call_tool(...)
```

#### **4. 模板方法模式 (Template Method)**
```python
# Tool 基类定义接口
class Tool:
    @property
    def name(self) -> str: ...  # 子类实现

    @property
    def parameters(self) -> dict: ...  # 子类实现

    async def execute(**kwargs) -> ToolResult: ...  # 子类实现
```

#### **5. 责任链模式 (Chain of Responsibility)**
```python
# 配置文件查找链
find_config_file():
  1. dev_config →
  2. user_config →
  3. package_config →
  4. None
```

### 5.2 代码分层

```
┌─────────────────────────────────────────────────────────────┐
│                    表现层 (Presentation)                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  CLI (cli.py) - 命令行界面、交互逻辑                 │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    业务层 (Business Logic)                   │
│  ┌─────────────────┐  ┌──────────────────────────────────┐  │
│  │  Agent          │  │  Tool Manager                    │  │
│  │  - 执行循环      │  │  - 工具注册                      │  │
│  │  - 消息管理      │  │  - 工具调度                      │  │
│  │  - Token 管理   │  │                                  │  │
│  └─────────────────┘  └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    服务层 (Service)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ LLM Client   │  │ MCP Loader   │  │ Skill Loader │      │
│  │ - API 调用    │  │ - MCP 连接    │  │ - Skill 加载  │      │
│  │ - 重试机制    │  │ - 工具转换    │  │              │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    数据层 (Data)                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ File System  │  │ MCP Servers  │  │ Config Files │      │
│  │              │  │              │  │              │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 架构优缺点

#### **优点**

✅ **模块化设计**
- 工具系统高度解耦，易于扩展
- LLM Provider 可切换
- 配置管理灵活

✅ **异步架构**
- 全异步执行，工具可并发
- 适合 I/O 密集型任务

✅ **可观测性**
- 完整的日志系统
- 详细的执行追踪
- Token 使用统计

✅ **健壮性**
- 重试机制
- 超时保护
- 优雅取消

✅ **标准化**
- MCP 协议支持
- 兼容 Anthropic/OpenAI API
- JSON Schema 参数定义

#### **缺点**

⚠️ **单 Agent 限制**
- 不支持多 Agent 协作
- 无法并行处理独立任务

⚠️ **状态管理**
- 消息历史全部在内存
- 重启后丢失上下文

⚠️ **错误处理**
- 工具执行失败后依赖 LLM 自我纠正
- 无重试策略

⚠️ **并发控制**
- 无工具并发执行限制
- 可能资源耗尽

---

## 6. 给新手的阅读建议

### 6.1 推荐阅读顺序

```
第一轮：理解整体流程 (30 分钟)
│
├─ 1. README.md                    # 了解项目功能
├─ 2. mini_agent/schema/schema.py   # 数据结构定义
├─ 3. mini_agent/tools/base.py      # 工具接口
└─ 4. mini_agent/agent.py           # 核心执行逻辑 (重点!)
│
第二轮：深入模块 (1 小时)
│
├─ 5. mini_agent/llm/llm_wrapper.py # LLM 客户端
├─ 6. mini_agent/tools/file_tools.py # 工具实现示例
├─ 7. mini_agent/config.py          # 配置管理
└─ 8. mini_agent/cli.py             # 入口和交互
│
第三轮：扩展功能 (1 小时)
│
├─ 9. mini_agent/tools/mcp_loader.py   # MCP 集成
├─ 10. mini_agent/tools/note_tool.py   # 持久化示例
└─ 11. tests/test_agent.py             # 测试示例
```

### 6.2 核心业务路径

**路径 1：用户输入 → Agent 响应**
```
cli.py:run_interactive()
  → agent.add_user_message()
  → agent.run()
    → llm.generate()
    → 返回 response
```

**路径 2：工具调用**
```
agent.run()
  → llm.generate() 返回 tool_calls
  → tool.execute(**arguments)
  → 添加 tool 消息
  → 下一轮 llm.generate()
```

**路径 3：Token 管理**
```
agent.run()
  → _estimate_tokens()
  → 超限？
    → _summarize_messages()
      → _create_summary()
```

### 6.3 调试技巧

```python
# 1. 启用详细日志
import logging
logging.basicConfig(level=logging.DEBUG)

# 2. 查看 Agent 内部状态
print(f"消息数量: {len(agent.messages)}")
print(f"Token 估算: {agent._estimate_tokens()}")
print(f"可用工具: {list(agent.tools.keys())}")

# 3. 查看日志文件
# 日志位置: ~/.mini-agent/log/
mini-agent
> /logs
# 选择最近的日志文件查看

# 4. 单步调试
# 在 agent.py 中设置断点:
import pdb; pdb.set_trace()
```

### 6.4 扩展建议

**添加新工具：**
1. 继承 `Tool` 基类
2. 实现 `name`, `description`, `parameters`
3. 实现 `async execute(**kwargs)` 返回 `ToolResult`
4. 在 `cli.py` 中注册

**添加新的 LLM Provider：**
1. 在 `llm/` 目录创建新文件
2. 继承 `LLMClientBase`
3. 实现 `generate()` 方法
4. 在 `llm_wrapper.py` 中注册

**自定义系统 Prompt：**
编辑 `mini_agent/config/system_prompt.md`

---

## 附录：关键代码片段

### A. Tool 实现模板

```python
from mini_agent.tools.base import Tool, ToolResult

class MyTool(Tool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "我的工具描述"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "参数1"
                }
            },
            "required": ["param1"]
        }

    async def execute(self, param1: str) -> ToolResult:
        try:
            # 执行逻辑
            result = f"处理结果: {param1}"
            return ToolResult(success=True, content=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

### B. 消息格式

```python
# 用户消息
Message(role="user", content="帮我创建一个文件")

# Assistant 响应（带工具调用）
Message(
    role="assistant",
    content="我来帮你创建文件",
    tool_calls=[
        ToolCall(
            id="call_123",
            function=FunctionCall(
                name="write_file",
                arguments={"path": "test.txt", "content": "hello"}
            )
        )
    ]
)

# 工具结果
Message(
    role="tool",
    content="文件已创建",
    tool_call_id="call_123",
    name="write_file"
)
```

---

**文档版本：** v1.0
**最后更新：** 2026-03-27
**维护者：** Mini-Agent Team

# Mini-Agent 持久化记忆系统 — 特性实现文档

## 概述

Mini-Agent 实现了跨会话持久化记忆系统，参考 Claude Code 的记忆架构。系统通过 Markdown 文件存储记忆，agent 复用现有文件工具（Read/Write/Edit）读写，无需专用记忆工具。

**核心设计原则：** 不新增工具，只做**启动时自动加载** + **告知 agent 记忆文件路径**。

---

## 架构

### 三层作用域

| 层级 | 文件路径 | 作用范围 | 写入方 | 是否必须存在 |
|------|---------|---------|--------|------------|
| 全局用户偏好 | `~/.mini-agent/MINI_AGENT.md` | 所有项目 | 用户手动创建 | 否 |
| 项目指令 | `<项目根目录>/MINI_AGENT.md` | 单个项目（可通过 git 共享） | 用户手动创建 | 否 |
| 自动记忆索引 | `~/.mini-agent/projects/<name>_<hash8>/memory/MEMORY.md` | 单个项目（本地） | Agent 运行时主动保存 | 否 |

三个文件都是可选的，不存在则对应 section 不注入。加载优先级：全局 → 项目 → 自动记忆。

### 存储结构

```
~/.mini-agent/
  MINI_AGENT.md                                    # 全局用户偏好
  projects/
    Mini-Agent_ac37145d/                           # <目录名>_<hash8>
      memory/
        MEMORY.md                                  # 记忆索引（前 200 行自动加载）
        debugging.md                               # 主题文件（按需读写）
        patterns.md
        ...

<项目根目录>/
  MINI_AGENT.md                                    # 项目指令
```

**路径计算规则：**
- `<目录名>`：git repo root 的目录名（如 `Mini-Agent`）
- `<hash8>`：项目完整路径 SHA256 的前 8 位（如 `hashlib.sha256("/Users/jone/Desktop/codespace/Mini-Agent".encode()).hexdigest()[:8]` → `ac37145d`）

### Git 仓库感知

通过 `git rev-parse --show-toplevel` 检测项目根目录：

- 从 git repo 内任意子目录启动 → 共享同一份记忆
- 不同的 git repo → 独立记忆目录
- 非 git 目录 → 回退使用 workspace 目录
- git 未安装 → 回退使用 workspace 目录

---

## 完整 System Prompt 拼接过程

Agent 最终看到的 system prompt 由 3 部分拼接而成：

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. system_prompt.md（静态模板）                                  │
│    文件：mini_agent/config/system_prompt.md                     │
│    内容：角色定义、工具说明、Skills、工作指南、Memory System 章节  │
│    处理：{SKILLS_METADATA} → 替换为 skill 列表                  │
│    位置：cli.py:589-609                                         │
├─────────────────────────────────────────────────────────────────┤
│ 2. Workspace 信息（运行时注入）                                  │
│    内容："\n\n## Current Workspace\n                           │
│           You are currently working in: `<绝对路径>`\n          │
│           All relative paths will be resolved relative to      │
│           this directory."                                      │
│    位置：agent.py:70-72                                         │
├─────────────────────────────────────────────────────────────────┤
│ 3. Memory Context（运行时注入）                                  │
│    来源：memory_manager.build_memory_context()                  │
│    内容：# Cross-Session Memory + 三层 section + footer         │
│    位置：agent.py:74-76                                         │
└─────────────────────────────────────────────────────────────────┘
```

**注入位置：** 追加在 system prompt 末尾。System prompt 在 `_summarize_messages()` 中被完整保留，确保持久化知识始终可用。

### Memory Context 输出示例（三个文件都存在时）

```markdown
# Cross-Session Memory

The following content was automatically loaded from persistent memory.
This information carries across sessions.

---

## Global User Preferences (from ~/.mini-agent/MINI_AGENT.md)

Always use uv for Python.
Reply in Chinese.

---

## Project Instructions (from MINI_AGENT.md)

Always use async/await for I/O operations.

---

## Persistent Memory Index (from MEMORY.md)

# Project Memory

## Architecture
- Tool-based design

## Key Files
- agent.py: Main agent class

Memory directory: `~/.mini-agent/projects/Mini-Agent_ac37145d/memory`
Use Read/Write/Edit tools to manage memory files in this directory.
Keep memory concise and well-organized.
```

### 真实场景（三个文件都不存在时）

`build_memory_context()` 返回空字符串 `""`，不注入任何 memory context。

---

## 核心组件

### MemoryManager

**文件：** `mini_agent/tools/memory_manager.py`

```
class MemoryManager:
    MAX_INDEX_LINES = 200                          # MEMORY.md 自动加载行数上限
    PROJECT_INSTRUCTION_FILENAME = "MINI_AGENT.md"
    GLOBAL_INSTRUCTION_FILE = ~/.mini-agent/MINI_AGENT.md

    __init__(workspace_dir)
        → self.workspace_dir = Path(workspace_dir).absolute()
        → self.project_root = _find_project_root()
        → self.memory_dir = _resolve_memory_dir()

    _find_project_root() → Path
        → subprocess.run(["git", "rev-parse", "--show-toplevel"])
        → 成功：返回 git repo root
        → 失败：返回 self.workspace_dir

    _resolve_memory_dir() → Path
        → name = project_root.name
        → hash8 = sha256(str(project_root))[:8]
        → ~/.mini-agent/projects/{name}_{hash8}/memory

    load_memory_index() → str | None
        → 读取 memory_dir/MEMORY.md
        → 超过 200 行截断 + 提示

    load_project_instructions() → str | None
        → 读取 project_root/MINI_AGENT.md

    load_global_instructions() → str | None
        → 读取 ~/.mini-agent/MINI_AGENT.md

    build_memory_context() → str
        → 收集三层内容，用 --- 分隔拼接
        → 添加 header + footer
        → 全部为空则返回 ""

    initialize_memory() → str
        → ensure_memory_dir() + build_memory_context()
```

---

## 数据流

```
会话启动
  │
  ▼
cli.py (line ~467-472)
  │  MemoryManager(workspace_dir)
  │  memory_context = manager.initialize_memory()
  │
  ▼
cli.py (line ~614)
  │  Agent(..., memory_context=memory_context)
  │
  ▼
agent.py (line 74-76)
  │  system_prompt += "\n\n" + memory_context
  │  messages = [Message(role="system", content=system_prompt)]
  │
  ▼
LLM 看到完整 system prompt（含记忆上下文）
  │
  ▼
LLM 运行中使用 write_file/read_file/edit_file 管理记忆文件
```

---

## 文件变更清单

| 文件 | 操作 | 改动量 | 说明 |
|------|------|-------|------|
| `mini_agent/tools/memory_manager.py` | 新建 | ~166 行 | MemoryManager 核心类 |
| `mini_agent/agent.py` | 修改 | +3 行 | 添加 `memory_context` 参数，注入到 system prompt |
| `mini_agent/cli.py` | 修改 | +10 行 | 初始化 MemoryManager，传入 Agent 构造函数 |
| `mini_agent/config.py` | 修改 | +3 行 | ToolsConfig 添加 `enable_memory` |
| `mini_agent/config/system_prompt.md` | 修改 | +6 行 | 添加 Memory System 章节 |
| `tests/test_memory.py` | 新建 | ~200 行 | 19 个单元测试 |

---

## 大小控制

| 文件 | 自动加载上限 | 说明 |
|------|------------|------|
| `MEMORY.md` | 200 行 | 超出部分截断并显示 `[Memory index truncated: showing 200 of N lines]` |
| `MINI_AGENT.md`（全局） | 无硬限制 | 用户自行管理，建议精简 |
| `MINI_AGENT.md`（项目） | 无硬限制 | 用户自行管理，建议精简 |
| 主题文件（`*.md`） | 不自动加载 | Agent 通过文件工具按需读取 |

Token 预算估计：三层全部填满时约 2000-5000 tokens，在 80000 token 限制内。

---

## 记忆保存机制

不使用专用记忆工具。LLM 通过系统提示词引导主动保存：

> 主动保存重要发现、模式、决策和用户偏好到记忆中。

Agent 使用标准文件工具操作记忆：
- `write_file` → 创建/更新记忆文件（如 `patterns.md`、`debugging.md`）
- `read_file` → 按需读取主题文件
- `edit_file` → 更新特定段落

**MEMORY.md** 作为索引文件，应保持精简（链接到各主题文件），详细内容放在主题文件中。

---

## 配置

记忆系统默认启用。可在 `config.yaml` 中关闭：

```yaml
tools:
  enable_memory: false
```

---

## 兼容性

- 与 `SessionNoteTool` / `RecallNoteTool`（基于 JSON 的会话笔记）共存，互不影响
- `enable_note` 和 `enable_memory` 独立控制
- MCP 知识图谱记忆服务器（`@modelcontextprotocol/server-memory`）可同时启用，作为实体关系存储的补充

---

## 与业界对比

| 特性 | Mini-Agent | Claude Code | Codex CLI | Windsurf |
|------|-----------|-------------|-----------|----------|
| 项目指令文件 | `MINI_AGENT.md` | `CLAUDE.md` | `AGENTS.md` | `.windsurfrules` |
| 全局用户偏好 | `~/.mini-agent/MINI_AGENT.md` | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | global_rules.md |
| 自动记忆 | `MEMORY.md`（200 行） | `MEMORY.md`（200 行） | 无 | Cascade Memories |
| Git 仓库感知 | 有 | 有 | 有 | 无 |
| 目录级条件加载 | 无 | `.claude/rules/` + glob | 嵌套 `AGENTS.md` | 无 |
| 自动整理合并 | 无 | Auto-dream | 无 | 未知 |

---

## 测试覆盖

`tests/test_memory.py` 共 19 个测试，全部通过：

| 类别 | 测试数 | 覆盖内容 |
|------|-------|---------|
| 路径解析 | 2 | hash 计算、路径冲突防止 |
| 目录管理 | 1 | 自动创建 |
| MEMORY.md 加载 | 3 | 不存在、存在、200 行截断 |
| 项目指令加载 | 2 | 不存在、存在 |
| 全局指令加载 | 2 | 不存在、存在 |
| 上下文构建 | 2 | 空场景、双文件场景 |
| 初始化流程 | 2 | 空初始化、有内容初始化 |
| Git 感知 | 4 | git repo、无 git、git 未安装、子目录共享 |
| 三层组装 | 1 | 全局 + 项目 + 记忆三层拼接顺序 |

运行命令：`uv run pytest tests/test_memory.py -v`

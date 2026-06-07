# Mini-Agent 记忆系统设计文档

## 概述

Mini-Agent 实现了一个跨会话持久化记忆系统，参考 Claude Code 的记忆架构设计。系统通过 Markdown 文件存储记忆，agent 直接复用现有的文件工具（Read/Write/Edit）进行读写，无需专用记忆工具。

## 架构

### 三层作用域

| 层级 | 文件路径 | 作用范围 | 写入方 |
|------|---------|---------|--------|
| 全局用户偏好 | `~/.mini-agent/MINI_AGENT.md` | 所有项目 | 用户手动创建 |
| 项目指令 | `<项目根目录>/MINI_AGENT.md` | 单个项目（可通过 git 共享给团队） | 用户手动创建 |
| 自动记忆索引 | `~/.mini-agent/projects/<name>_<hash8>/memory/MEMORY.md` | 单个项目（本地） | Agent 主动保存 |

加载优先级：全局 → 项目 → 自动记忆。

### 存储结构

```
~/.mini-agent/
  MINI_AGENT.md                              # 全局用户偏好（所有项目共享）
  projects/
    <项目名>_<hash8>/
      memory/
        MEMORY.md                            # 记忆索引（自动加载前 200 行）
        <主题>.md                            # 主题文件（按需通过文件工具读写）

<项目根目录>/
  MINI_AGENT.md                              # 项目指令
```

- **`<项目名>`**：取 git repo root 的目录名，便于识别。
- **`<hash8>`**：项目完整路径 SHA256 的前 8 位，防止路径冲突（如 `/Users/jone/a_b` 和 `/Users/jone/a/b`）。

### Git 仓库感知

系统通过 `git rev-parse --show-toplevel` 检测项目根目录：

- 从 git repo 内任意子目录启动 Mini-Agent，都共享同一份记忆。
- 不同的 git repo 拥有独立的记忆目录。
- 非 git 目录回退到使用 workspace 目录作为项目根。

## 核心组件

### MemoryManager（`mini_agent/tools/memory_manager.py`）

```python
class MemoryManager:
    MAX_INDEX_LINES = 200                    # MEMORY.md 自动加载行数上限

    def __init__(self, workspace_dir)        # 解析项目根 + 记忆目录
    def _find_project_root() -> Path         # Git 仓库根目录检测
    def _resolve_memory_dir() -> Path        # 计算记忆目录路径
    def ensure_memory_dir() -> Path          # 创建记忆目录
    def load_memory_index() -> str | None    # 加载 MEMORY.md（前 200 行）
    def load_project_instructions() -> str | None  # 加载项目 MINI_AGENT.md
    def load_global_instructions() -> str | None   # 加载全局 MINI_AGENT.md
    def build_memory_context() -> str        # 构建完整记忆上下文
    def initialize_memory() -> str           # 会话启动时一次性初始化
```

## 数据流

```
会话启动
    |
    v
cli.py: MemoryManager(workspace_dir)
    |
    +-- _find_project_root()               # git rev-parse --show-toplevel
    +-- _resolve_memory_dir()              # 哈希项目路径 -> 记忆目录
    +-- initialize_memory()
          |
          +-- ensure_memory_dir()           # 创建目录（如不存在）
          +-- build_memory_context()
                |
                +-- load_global_instructions()    # ~/.mini-agent/MINI_AGENT.md
                +-- load_project_instructions()   # <项目根>/MINI_AGENT.md
                +-- load_memory_index()           # memory/MEMORY.md（200 行）
                |
                v
          拼接为上下文字符串
                |
                v
agent.py: 注入到系统提示词
    |
    v
LLM 看到记忆上下文 + 可使用 Read/Write/Edit 管理记忆文件
```

## 上下文注入

记忆上下文追加在系统提示词末尾。系统提示词在消息历史摘要时会被完整保留，确保持久化知识始终可用。

注入示例：

```markdown
# Cross-Session Memory

The following content was automatically loaded from persistent memory.
This information carries across sessions.

---

## Global User Preferences (from ~/.mini-agent/MINI_AGENT.md)

Always use uv for Python package management.
Reply in Chinese.

---

## Project Instructions (from MINI_AGENT.md)

Always use async/await for I/O operations.

---

## Persistent Memory Index (from MEMORY.md)

# Project Memory

## Architecture
- Uses tool-based architecture with JSON Schema parameters
- Agent loop with token management and auto-summarization

## Key Files
- agent.py: Main agent class
- tools/: All tool implementations

Memory directory: `~/.mini-agent/projects/Mini-Agent_a1b2c3d4/memory/`
Use Read/Write/Edit tools to manage memory files in this directory.
Keep memory concise and well-organized.
```

## 大小控制

| 文件 | 自动加载上限 | 说明 |
|------|------------|------|
| `MEMORY.md` | 200 行 | 超出部分截断并显示提示 |
| `MINI_AGENT.md`（全局） | 无硬限制 | 用户自行管理，建议精简 |
| `MINI_AGENT.md`（项目） | 无硬限制 | 用户自行管理，建议精简 |
| 主题文件（`*.md`） | 不自动加载 | Agent 通过文件工具按需读取 |

## 记忆保存机制

不使用专用记忆工具。LLM 通过系统提示词的引导主动保存记忆：

> 主动保存重要发现、模式、决策和用户偏好到记忆中。

Agent 使用标准文件工具操作记忆：
- `write_file` — 创建/更新记忆文件
- `read_file` — 按需读取主题文件
- `edit_file` — 更新特定段落

## 与其他系统对比

| 特性 | Mini-Agent | Claude Code | Codex CLI | Windsurf |
|------|-----------|-------------|-----------|----------|
| 项目指令文件 | `MINI_AGENT.md` | `CLAUDE.md` | `AGENTS.md` | `.windsurfrules` |
| 全局用户偏好 | `~/.mini-agent/MINI_AGENT.md` | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | global_rules.md |
| 自动记忆 | `MEMORY.md`（200 行） | `MEMORY.md`（200 行） | 无 | Cascade Memories |
| Git 仓库感知 | 有 | 有 | 有 | 无 |
| 目录级条件加载 | 无 | `.claude/rules/` + glob | 嵌套 `AGENTS.md` | 无 |
| 自动整理合并 | 无 | Auto-dream | 无 | 未知 |

## 配置

记忆系统默认启用。可在 `config.yaml` 中关闭：

```yaml
tools:
  enable_memory: false
```

## 兼容性

- 与现有的 `SessionNoteTool` / `RecallNoteTool`（基于 JSON 的会话笔记）共存，互不影响。
- `enable_note` 和 `enable_memory` 独立控制。
- MCP 知识图谱记忆服务器（`@modelcontextprotocol/server-memory`）可同时启用，作为实体关系存储的补充。

## 测试覆盖

`tests/test_memory.py` 共 19 个测试：

- 路径解析和哈希冲突防止（2 个）
- 目录创建（1 个）
- MEMORY.md 加载和截断（3 个）
- 项目指令加载（2 个）
- 全局指令加载（2 个）
- 上下文构建和拼接顺序（2 个）
- 完整初始化流程（2 个）
- Git 仓库根目录检测和回退（3 个）
- 子目录记忆共享（1 个）
- 三层上下文组装（1 个）

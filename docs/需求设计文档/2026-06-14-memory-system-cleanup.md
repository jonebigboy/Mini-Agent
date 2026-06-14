# Mini-Agent 记忆系统清理与标准化 — 特性设计文档

**日期**: 2026-06-14
**分支**: `fsw`
**提交范围**: `07f65ba` → `3d14835`（11 个提交）
**变更规模**: 19 个文件，+140 行，-587 行（净减 447 行）

---

## 1. 背景与问题

### 1.1 原有三套记忆系统并存

Mini-Agent 在此特性之前存在 **3 套功能高度重叠的持久化记忆机制**，LLM 无法判断该用哪个：

| 系统 | 存储格式 | 位置 | 写入方 | 问题 |
|------|---------|------|--------|------|
| `SessionNoteTool` / `RecallNoteTool` | JSON 数组 | `<workspace>/.agent_memory.json` | LLM 调用专用工具 | 污染 workspace、JSON 不适合 LLM 编辑、无 git 感知、无去重 |
| `MemoryManager` | Markdown | `~/.mini-agent/projects/<hash>/memory/MEMORY.md` | LLM 用文件工具 | ✅ 设计正确，与 Claude Code 一致 |
| MCP Knowledge Graph | 图数据库 | MCP server 外部进程 | LLM 调用 MCP 工具 | 启用但无引导、业界零应用、浪费 token |

**核心痛点**：LLM 没有任何依据判断该用哪个记忆系统，导致随机选择或三处重复写入。

### 1.2 业界调研结论

调研 8 款主流 code agent（Claude Code、Cursor、Cline、Aider、Continue、Codex CLI、Gemini CLI、Windsurf）+ OpenClaw（250K+ stars 开源 Agent 框架）：

1. **存储格式**：8 款主流 code agent **全部使用 Markdown**，知识图谱在生产环境零应用
2. **规则 vs 记忆分离**：Claude Code 最严格区分（CLAUDE.md 用户规则 / MEMORY.md LLM 记忆）
3. **LLM 主动写记忆**：Claude Code / Windsurf / Gemini CLI 有实践，共同特征是 Markdown + 用户可审计 + prompt 引导
4. **`AGENTS.md` 跨工具标准**：Codex CLI / OpenCode 推动，新项目应采用（而非专有的 `CLAUDE.md`）
5. **全局 LLM 记忆**：业界（含 Claude Code）均不实现，因跨项目污染风险高

---

## 2. 设计决策

基于调研结果，选择 **A 档（最小清理）** 路线 — 只做减法，对齐 Claude Code 三档模式，不引入新机制。

| # | 决策点 | 选定方案 | 理由 |
|---|--------|---------|------|
| 1 | 重构深度 | A 档（最小清理） | YAGNI，先清理再说扩展 |
| 2 | MEMORY.md 写入机制 | 纯靠 LLM 自觉（prompt 引导） | 与 Claude Code 一致，无新代码 |
| 3 | SessionNoteTool 代码 | 硬删除 | JSON 是反模式，MemoryManager 全面胜出 |
| 4 | SessionNoteTool 文档/示例 | 保留 + 加 deprecation 注释 | 作为模式参考，未来再清理 |
| 5 | MCP 知识图谱 | 保留配置，`disabled: true` | 删除成本低，保留便于未来灵活启用 |
| 6 | `MINI_AGENT.md` 改名 | 硬改为 `AGENTS.md`，无 fallback | 跟随业界跨工具标准；演示项目无历史包袱 |
| 7 | 写入大小控制 | Prompt 加硬性数字（150/100 行） | 软约束但明确，业界实践表明数字比"keep concise"有效 |
| 8 | 三档架构 | 保持当前（不加全局 LLM 记忆） | 与 Claude Code 一致；全局 LLM 记忆有跨项目污染风险 |

---

## 3. 架构

### 3.1 变更前 — 三套并存

```
┌─────────────────────────────────────────────────────────┐
│ 系统 1: SessionNoteTool (JSON)                           │
│   workspace/.agent_memory.json                           │
│   工具: record_note(category, content)                   │
│   工具: recall_notes(category)                           │
├─────────────────────────────────────────────────────────┤
│ 系统 2: MemoryManager (Markdown)                         │
│   ~/.mini-agent/projects/<hash>/memory/MEMORY.md         │
│   ~/.mini-agent/MINI_AGENT.md (全局规则)                 │
│   <project>/MINI_AGENT.md (项目规则)                     │
│   加载: 启动时注入 system prompt                          │
│   写入: LLM 用 read_file/write_file/edit_file             │
├─────────────────────────────────────────────────────────┤
│ 系统 3: MCP Knowledge Graph (图数据库)                    │
│   @modelcontextprotocol/server-memory                    │
│   工具: create_entities, create_relations, query_nodes...│
└─────────────────────────────────────────────────────────┘
```

### 3.2 变更后 — 单一 Markdown 路径

```
┌─────────────────────────────────────────────────────────┐
│ 档 1: 全局规则（用户手写）                                 │
│   文件: ~/.mini-agent/AGENTS.md                           │
│   作用域: 所有项目                                         │
│   内容: "回复用中文"、"用 uv 不用 pip" 等跨项目偏好        │
├─────────────────────────────────────────────────────────┤
│ 档 2: 项目规则（用户手写）                                 │
│   文件: <project_root>/AGENTS.md                          │
│   作用域: 单项目（可 commit 到 git 共享）                  │
│   内容: 项目特定的编码规范、架构约定                        │
├─────────────────────────────────────────────────────────┤
│ 档 3: 项目 LLM 记忆（LLM 自动写）                          │
│   文件: ~/.mini-agent/projects/<name>_<hash8>/memory/     │
│         MEMORY.md  (索引，前 200 行自动加载)               │
│         <topic>.md  (主题文件，按需读写)                    │
│   作用域: 单项目（本地，不进 git）                          │
│   写入方: LLM 通过 read_file/write_file/edit_file 操作     │
└─────────────────────────────────────────────────────────┘

加载优先级: 档 1 → 档 2 → 档 3
全部注入 system prompt（在 summarization 时完整保留）
```

### 3.3 数据流

```
会话启动
  │
  ▼
cli.py
  │  MemoryManager(workspace_dir)
  │  memory_context = manager.initialize_memory()
  │    ├─ load_global_instructions()    # ~/.mini-agent/AGENTS.md
  │    ├─ load_project_instructions()   # <project>/AGENTS.md
  │    └─ load_memory_index()           # memory/MEMORY.md (200 行截断)
  │
  ├─ _warn_legacy_files(workspace_dir)  # 检测旧文件并提示
  │
  ▼
system_prompt = system_prompt.md (含 Memory System 章节)
              + workspace 信息
              + memory_context
  │
  ▼
Agent 运行
  │  LLM 通过 read_file/write_file/edit_file 操作 memory/ 目录
  │  不再有 record_note / recall_notes / 知识图谱工具（KG disabled）
```

---

## 4. 代码变更明细

### 4.1 总览

| 类别 | 文件数 | 净行数 |
|------|--------|--------|
| 删除 | 2 | -347 |
| 修改（代码） | 5 | +27 / -27 |
| 修改（配置） | 2 | +13 / -12 |
| 修改（测试） | 4 | +85 / -293 |
| 修改（示例） | 2 | +5 / -17 |
| 修改（文档） | 5 | +10 / -26 |
| 新增（测试） | 1 | +63 |
| **合计** | **19** | **+140 / -587** |

### 4.2 删除的文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `mini_agent/tools/note_tool.py` | 213 行 | `SessionNoteTool` + `RecallNoteTool` 实现，JSON 存储 |
| `tests/test_note_tool.py` | 134 行 | 对应的测试文件 |

### 4.3 修改 — 源代码

#### `mini_agent/tools/__init__.py`（-3 行）

移除 note_tool 的导入和 `__all__` 导出：

```python
# 删除:
from .note_tool import RecallNoteTool, SessionNoteTool

# __all__ 中删除:
"SessionNoteTool",
"RecallNoteTool",
```

#### `mini_agent/cli.py`（+20 / -6 行）

**移除 SessionNoteTool import（第 39 行原位置）**：

```python
# 删除:
from mini_agent.tools.note_tool import SessionNoteTool
```

**移除 `enable_note` 加载分支（原 517-519 行）**：

```python
# 删除整个分支:
if config.tools.enable_note:
    tools.append(SessionNoteTool(memory_file=str(workspace_dir / ".agent_memory.json")))
    print(f"{Colors.GREEN}✅ Loaded session note tool{Colors.RESET}")
```

**新增 `_warn_legacy_files()` 函数（482-497 行）**：

```python
def _warn_legacy_files(workspace_dir: Path) -> None:
    """Print warnings for deprecated files from previous Mini-Agent versions."""
    legacy_global = Path.home() / ".mini-agent" / "MINI_AGENT.md"
    if legacy_global.exists():
        print(
            f"{Colors.YELLOW}⚠️  Detected {legacy_global}. "
            f"Please rename to AGENTS.md to match the new convention.{Colors.RESET}"
        )

    legacy_notes = workspace_dir / ".agent_memory.json"
    if legacy_notes.exists():
        print(
            f"{Colors.YELLOW}ℹ️  Found {legacy_notes} (legacy SessionNoteTool data). "
            f"Safe to delete — SessionNoteTool has been removed.{Colors.RESET}"
        )
```

**新增调用点（记忆系统初始化之后，637 行）**：

```python
    # 4.6 Legacy file detection (warn about deprecated files)
    _warn_legacy_files(workspace_dir)
```

#### `mini_agent/config.py`（-2 行）

移除 `enable_note` 字段定义和 YAML 解析：

```python
# ToolsConfig 类中删除:
enable_note: bool = True

# from_yaml 方法中删除:
enable_note=tools_data.get("enable_note", True),
```

#### `mini_agent/tools/memory_manager.py`（+8 / -8 行）

文件名常量重命名：

```python
# 变更前:
PROJECT_INSTRUCTION_FILENAME = "MINI_AGENT.md"
GLOBAL_INSTRUCTION_FILE = Path.home() / ".mini-agent" / "MINI_AGENT.md"

# 变更后:
PROJECT_INSTRUCTION_FILENAME = "AGENTS.md"
GLOBAL_INSTRUCTION_FILE = Path.home() / ".mini-agent" / "AGENTS.md"
```

同步更新 docstring 和 `build_memory_context()` 中的 header 字符串（`"from ~/.mini-agent/MINI_AGENT.md"` → `"from ~/.mini-agent/AGENTS.md"`）。

### 4.4 修改 — 配置

#### `mini_agent/config/system_prompt.md`（+10 / -5 行）

替换原 Memory System 章节为带硬性约束的版本：

```markdown
### Memory System

Cross-session memory is auto-loaded into your context. Manage it with standard file tools
(read_file / write_file / edit_file) — no dedicated memory tools.

#### Rules
- `MEMORY.md`: **max 150 lines**. Topic files (`*.md`): **max 100 lines** each.
- Before writing: **check for duplicates**; near limits, **consolidate** (merge or demote to topic files).
- Write only durable facts: user preferences, confirmed conventions, decisions+rationale, gotchas.
- Skip: ephemeral task context, info already in AGENTS.md/code, speculation, duplicates.
- When unsure, don't write — noisy memory is worse than no memory.
```

**关键改进**：
- 硬性数字（150/100 行）替代模糊的"keep concise"
- 明确去重和整合指令
- "何时不写"清单防止 MEMORY.md 变垃圾场
- 低于 200 行自动加载上限，留 50 行缓冲

#### `mini_agent/config/config-example.yaml`（-1 行）

移除 `enable_note` 配置项：

```yaml
# 删除:
enable_note: true        # Session note tool (SessionNoteTool)
```

#### `mini_agent/config/mcp.json`（gitignored，本地变更）

```json
// memory 条目的 disabled 字段: false → true
"disabled": true
```

跟踪模板 `mini_agent/config/mcp-example.json` 已有 `"disabled": true`。

### 4.5 修改 — 测试

#### `tests/test_note_tool.py` — 删除（-134 行）

整个文件删除，测试的类已不存在。

#### `tests/test_memory.py`（+11 / -11 行）

所有 `MINI_AGENT.md` → `AGENTS.md`（11 处字符串引用 + docstring）。

#### `tests/test_session_integration.py`（-21 行）

- 移除 `from mini_agent.tools.note_tool import ...`
- 从 tools 列表移除 `SessionNoteTool()`
- 删除 `test_session_note_persistence` 测试函数

#### `tests/test_integration.py`（-138 行）

- 移除 `note_tool` import 和 `json` import（后者不再使用）
- 从 `test_basic_agent_usage` 移除 SessionNoteTool 块
- 删除 `test_session_memory_demo` 测试函数（整个函数仅测试 SessionNoteTool）
- 更新 `main()` 移除对已删函数的调用

#### `tests/test_legacy_detection.py` — 新增（+63 行）

4 个新测试覆盖 `_warn_legacy_files()` 函数：

| 测试 | 场景 |
|------|------|
| `test_warn_legacy_files_no_files` | 无旧文件 → 无输出 |
| `test_warn_legacy_files_global_mini_agent` | 全局 MINI_AGENT.md 存在 → 警告输出 |
| `test_warn_legacy_files_agent_memory_json` | workspace 有 .agent_memory.json → 提示输出 |
| `test_warn_legacy_files_both_exist` | 两者都存在 → 两条消息 |

### 4.6 修改 — 示例

#### `examples/03_session_notes.py`（+4 行）

文件顶部加 deprecation 注释，代码不动：

```python
# DEPRECATED: SessionNoteTool has been removed from Mini-Agent core.
# This example is preserved as a conceptual reference for pluggable
# storage backend patterns. The current memory system uses
# MemoryManager + Markdown files. See docs/memory-system-design.md.
```

**已知状态**：此文件 import 已删的 `SessionNoteTool`，运行会失败。作为可插拔后端的模式参考保留。

#### `examples/04_full_agent.py`（+1 / -19 行）

- 移除 `from mini_agent.tools.note_tool import ...`
- 移除 Session Note tools 加载块（9 行）
- 替换为单行注释：`# SessionNoteTool removed — use MemoryManager + AGENTS.md instead.`
- 移除残留的 `memory_file` 引用块（9 行，demo 结束时显示 session notes 的逻辑）

### 4.7 修改 — 文档

| 文件 | 改动 |
|------|------|
| `CLAUDE.md`（gitignored） | 移除 SessionNoteTool 提及；`MINI_AGENT.md` → `AGENTS.md`；MCP memory 标注"已禁用"；测试命令更新 |
| `docs/memory-system-design.md` | 全面更新：文件名、移除 SessionNoteTool 共存段落、更新对比表、兼容性段落改为"MemoryManager 唯一" |
| `docs/PRODUCTION_GUIDE.md` | Context Management 行：`SessionNoteTool` → `MemoryManager` |
| `docs/PRODUCTION_GUIDE_CN.md` | 同上（中文版） |
| `docs/run_agent_analysis.md` | 移除 SessionNoteTool 提及 |
| `ARCHITECTURE_ANALYSIS.md` | 架构图中"笔记工具" → "记忆系统 (MemoryManager)" |
| `docs/DEVELOPMENT_GUIDE.md` | **3.3 节保留不动**（用户决策，SessionNoteTool 存储后端示例作为概念参考） |
| `docs/DEVELOPMENT_GUIDE_CN.md` | 同上 |

---

## 5. system_prompt.md 拼接结构

Agent 最终看到的 system prompt 由 3 部分拼接：

```
┌─────────────────────────────────────────────────────────────┐
│ 1. system_prompt.md（静态模板）                               │
│    - 角色定义、工具说明、Skills、工作指南                       │
│    - Memory System 章节（新增硬性约束）                         │
│    位置: mini_agent/config/system_prompt.md                  │
├─────────────────────────────────────────────────────────────┤
│ 2. Workspace 信息（运行时注入）                                │
│    位置: agent.py                                            │
├─────────────────────────────────────────────────────────────┤
│ 3. Memory Context（运行时注入）                                │
│    # Cross-Session Memory                                    │
│    ## Global User Preferences (from ~/.mini-agent/AGENTS.md) │
│    ## Project Instructions (from AGENTS.md)                  │
│    ## Persistent Memory Index (from MEMORY.md)               │
│    位置: memory_manager.build_memory_context()               │
└─────────────────────────────────────────────────────────────┘
```

System prompt 在 `_summarize_messages()` 中被完整保留，确保持久化知识在 token 压缩后仍可用。

---

## 6. 提交历史

```
3d14835 test: add unit tests for _warn_legacy_files; fix MemoryManager label
fe03d33 feat: add legacy file detection for MINI_AGENT.md and .agent_memory.json
382e30c docs: update all docs for AGENTS.md rename and SessionNoteTool removal
ea50ec1 fix: remove residual memory_file reference from full_agent example
381cb51 docs: deprecate session_notes example, update full_agent example
ccfe750 docs: strengthen Memory System prompt with hard size limits and dedup rules
841e99c refactor: rename MINI_AGENT.md to AGENTS.md (industry standard)
f1bf470 chore: remove unused json import from test_integration.py
bdea091 test: remove SessionNoteTool references from test suite
740624a refactor: remove enable_note config and SessionNoteTool loading
07f65ba refactor: remove SessionNoteTool source code and imports
```

---

## 7. 测试

### 7.1 测试覆盖

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `tests/test_memory.py` | 19 | MemoryManager 三层加载、git 感知、路径解析 |
| `tests/test_legacy_detection.py` | 4 | `_warn_legacy_files()` 四种场景 |
| `tests/test_session_integration.py` | 多轮对话、历史管理 | 已移除 SessionNoteTool 相关测试 |
| `tests/test_integration.py` | 基础 agent 用法 | 已移除 SessionNoteTool 相关测试 |
| `tests/test_agent.py` | Agent 核心 | 无 SessionNoteTool 依赖 |

### 7.2 测试结果

```
218 passed, 3 skipped, 0 failures
```

3 个 skipped 是需要 API key 的集成测试。

---

## 8. 迁移说明

### 8.1 对现有用户的影响

**SessionNoteTool 数据**：workspace 下的 `.agent_memory.json` 不再被读取。启动时检测到会打印提示。用户可安全删除该文件。

**`MINI_AGENT.md` 重命名**：
- 项目根目录的 `MINI_AGENT.md`（如果有）不会被自动读取。需手动重命名为 `AGENTS.md`。
- 全局 `~/.mini-agent/MINI_AGENT.md`（如果有）同理。启动时检测到会打印迁移提示。
- MemoryManager **硬改名，无 fallback** — 不存在 `MINI_AGENT.md` 的回退读取逻辑。

**MCP 知识图谱**：`mcp.json` 中的 memory 服务器已禁用。如需重新启用，将 `disabled` 改回 `false`。

### 8.2 配置兼容性

`config.yaml` 中的 `enable_note: true` 字段会被静默忽略（`ToolsConfig` 不再有该字段）。不影响启动，但建议手动删除该行。

---

## 9. 已知遗留问题（接受）

| # | 问题 | 原因 | 影响 |
|---|------|------|------|
| 1 | `examples/03_session_notes.py` 运行失败 | import 已删的 `SessionNoteTool` | 作为模式参考保留，顶部有 deprecation 注释 |
| 2 | `docs/DEVELOPMENT_GUIDE.md` 3.3 节引用已删类 | 用户决策保留 | 概念性参考，未来再清理 |
| 3 | `config.yaml` 中 `enable_note` 遗留 | gitignored 本地文件 | 静默忽略，无功能影响 |

---

## 10. 范围外（留待后续）

| 项目 | 推迟到 | 理由 |
|------|--------|------|
| 每日日志层 `memory/YYYY-MM-DD.md` | B 档 | 需要新加载/检索逻辑 |
| 语义检索（向量 + BM25） | C 档 | 需嵌入模型 + SQLite 索引 |
| Auto memory flush（压缩前保存） | C 档 | 需要额外 LLM 调用 |
| Dreaming 整合机制 | C 档 | 工程复杂度高 |
| 全局 LLM 记忆（第四档） | 不做 | 业界无先例，跨项目污染风险 |
| `MemoryBackend` Protocol 抽象 | 不做 | YAGNI |
| `~/.mini-agent/` 目录改名 | 不做 | 仅文件名标准化，避免范围扩张 |

---

## 11. 与业界对比（变更后）

| 特性 | Mini-Agent | Claude Code | Codex CLI | OpenClaw |
|------|-----------|-------------|-----------|----------|
| 项目指令文件 | `AGENTS.md` | `CLAUDE.md` | `AGENTS.md` | — |
| 全局用户偏好 | `~/.mini-agent/AGENTS.md` | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | — |
| 自动记忆 | `MEMORY.md`（200 行截断） | `MEMORY.md`（200 行） | 无 | `MEMORY.md` + 每日日志 |
| LLM 自动写入 | ✅ prompt 引导 | ✅ prompt + auto-dream | ❌ | ✅ dreaming 整合 |
| Git 仓库感知 | ✅ | ✅ | ✅ | — |
| 存储格式 | Markdown | Markdown | Markdown | Markdown + SQLite |
| 写入大小约束 | ✅ 150/100 行硬性 | 软引导 | — | 自动提升/淘汰 |
| 知识图谱 | disabled（保留配置） | 无 | 无 | 无 |

Mini-Agent 变更后的记忆系统与 Claude Code 高度对齐，在写入大小约束上甚至比 Claude Code 更严格（硬性数字 vs 软引导）。

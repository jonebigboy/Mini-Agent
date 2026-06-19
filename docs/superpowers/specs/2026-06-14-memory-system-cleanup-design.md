# Mini-Agent 记忆系统清理与标准化 — 设计文档

**日期**: 2026-06-14
**状态**: 已批准（待实现）
**范围**: A 档（最小清理）
**作者**: brainstorming session 产出

---

## 1. 目标

让 Mini-Agent 记忆系统与业界主流 code agent（以 Claude Code 为标杆）对齐：

- **单一 Markdown 记忆路径** — 删除 JSON 笔记（SessionNoteTool）和知识图谱（MCP memory）两条冗余路径
- **三档清晰分工** — 全局规则、项目规则、项目 LLM 记忆，与 Claude Code 完全对齐
- **文件名标准化** — `MINI_AGENT.md` → `AGENTS.md`（跟随业界跨工具标准）
- **明确 LLM 写入引导** — system_prompt.md 新增 Memory System 章节，含硬性行数约束

## 2. 背景

### 2.1 现状问题

当前 Mini-Agent 存在 **3 套功能重叠的记忆系统**：

| 系统 | 存储 | 写入方 | 问题 |
|------|------|--------|------|
| `SessionNoteTool` / `RecallNoteTool` | JSON 文件（workspace 内） | LLM 调用工具 | 污染 workspace、JSON 不适合 LLM 编辑、无 git 感知、无去重 |
| `MemoryManager` | Markdown（中央目录） | LLM 用文件工具 | ✅ 设计正确，与 Claude Code 一致 |
| MCP Knowledge Graph | 外部图数据库 | LLM 调用 MCP 工具 | 启用但无引导、业界零应用、浪费 token |

主要痛点：**LLM 不知道该用哪个**，导致随机选择或三处重复写入。

### 2.2 业界调研结论

调研 8 款主流 code agent + OpenClaw：

1. **存储格式**：8 款全部使用 Markdown，知识图谱在生产环境零应用
2. **规则 vs 记忆分离**：Claude Code 最严格区分（CLAUDE.md 用户规则 / MEMORY.md LLM 记忆）
3. **LLM 主动写记忆**：Claude Code / Windsurf / Gemini CLI 有实践，共同特征是 Markdown + 用户可审计 + prompt 引导
4. **`AGENTS.md` 跨工具标准**：Codex CLI / OpenCode 推动，新项目应采用
5. **全局 LLM 记忆**：业界（含 Claude Code）均不实现，因跨项目污染风险高

### 2.3 选定方向

**A 档（最小清理）**：只做减法，对齐 Claude Code 三档模式，不引入新机制（每日日志、语义检索、auto-flush 留待 B/C 档）。

## 3. 关键决策

| # | 决策点 | 选定方案 | 理由 |
|---|--------|---------|------|
| 1 | 重构深度 | A 档 | YAGNI，先清理再说扩展 |
| 2 | MEMORY.md 写入机制 | 纯靠 LLM 自觉 | 与 Claude Code 一致，无新代码 |
| 3 | SessionNoteTool 代码 | 硬删除 | JSON 是反模式，MemoryManager 全面胜出 |
| 4 | SessionNoteTool 文档/示例 | 保留 + 加 deprecation 注释 | 作为模式参考，未来再清理 |
| 5 | MCP 知识图谱 | 保留配置，`disabled: true` | 删除成本低，保留便于未来灵活启用 |
| 6 | `MINI_AGENT.md` 改名 | 硬改为 `AGENTS.md`，无 fallback | 跟随业界标准；演示项目无历史包袱 |
| 7 | 写入大小控制 | Prompt 加硬性数字（150/100 行） | 软约束但明确，业界实践表明数字比"keep concise"有效 |
| 8 | 三档架构 | 保持当前（不加全局 LLM 记忆） | 与 Claude Code 一致；全局 LLM 记忆有跨项目污染风险 |

## 4. 架构

### 4.1 三档模型（变更后）

```
┌──────────────────────────────────────────────────────────────┐
│ 档 1: 全局规则（用户手写）                                      │
│ 文件: ~/.mini-agent/AGENTS.md                                 │
│ 作用域: 所有项目                                                │
│ 内容: "回复用中文"、"用 uv 不用 pip" 等跨项目偏好               │
├──────────────────────────────────────────────────────────────┤
│ 档 2: 项目规则（用户手写）                                      │
│ 文件: <project_root>/AGENTS.md                                 │
│ 作用域: 单项目（可 commit 到 git 共享）                         │
│ 内容: 项目特定的编码规范、架构约定                              │
├──────────────────────────────────────────────────────────────┤
│ 档 3: 项目 LLM 记忆（LLM 自动写）                               │
│ 文件: ~/.mini-agent/projects/<name>_<hash8>/memory/MEMORY.md   │
│       ~/.mini-agent/projects/<name>_<hash8>/memory/<topic>.md  │
│ 作用域: 单项目（本地，不进 git）                                │
│ 内容: LLM 在交互中学到的偏好、决策、gotcha                      │
│ 写入方: LLM 通过 read_file/write_file/edit_file 工具操作        │
└──────────────────────────────────────────────────────────────┘
```

**加载优先级**：档 1 → 档 2 → 档 3，全部注入 system prompt（见 4.3）。

### 4.2 数据流（变更后）

```
会话启动
  │
  ▼
cli.py
  │  memory_manager = MemoryManager(workspace_dir)
  │  memory_context = memory_manager.initialize_memory()
  │    ├─ load_global_instructions()    # ~/.mini-agent/AGENTS.md
  │    ├─ load_project_instructions()   # <project>/AGENTS.md
  │    └─ load_memory_index()           # memory/MEMORY.md (200 行截断)
  │
  ▼
system_prompt = system_prompt.md (含 Memory System 章节)
              + workspace 信息
              + memory_context
  │
  ▼
Agent 运行
  │  LLM 通过 read_file/write_file/edit_file 操作 memory/ 目录
  │  不再有 record_note / recall_notes / 知识图谱工具
```

### 4.3 system prompt 拼接顺序

```
┌─────────────────────────────────────────────────────────┐
│ 1. system_prompt.md（静态模板）                          │
│    - 角色定义、工具说明、Skills、工作指南                  │
│    - Memory System 章节（新增，见 §5）                    │
├─────────────────────────────────────────────────────────┤
│ 2. Workspace 信息（运行时注入）                           │
├─────────────────────────────────────────────────────────┤
│ 3. Memory Context（运行时注入）                           │
│    # Cross-Session Memory                                │
│    ## Global User Preferences (from ~/.mini-agent/AGENTS.md) │
│    ## Project Instructions (from AGENTS.md)              │
│    ## Persistent Memory Index (from MEMORY.md)           │
└─────────────────────────────────────────────────────────┘
```

## 5. system_prompt.md 新增章节

在 `mini_agent/config/system_prompt.md` 末尾追加：

```markdown
## Memory System

Cross-session memory is auto-loaded into your context. Manage it with standard file tools
(read_file / write_file / edit_file) — no dedicated memory tools.

### Rules
- `MEMORY.md`: **max 150 lines**. Topic files (`*.md`): **max 100 lines** each.
- Before writing: **check for duplicates**; near limits, **consolidate** (merge or demote to topic files).
- Write only durable facts: user preferences, confirmed conventions, decisions+rationale, gotchas.
- Skip: ephemeral task context, info already in AGENTS.md/code, speculation, duplicates.
- When unsure, don't write — noisy memory is worse than no memory.
```

**设计要点**：
- ~10 行，token 开销可控
- 硬性数字 150/100（低于 200 行自动加载上限，留缓冲）
- 明确"何时不写"，防止 MEMORY.md 变垃圾场
- 不强制结构，允许灵活

## 6. 详细变更清单

### 6.1 删除（2 个文件）

| 文件 | 理由 |
|------|------|
| `mini_agent/tools/note_tool.py` | SessionNoteTool / RecallNoteTool 代码 |
| `tests/test_note_tool.py` | 测试已删类，pytest collection 会报错 |

**注**：`examples/03_session_notes.py` 不删除，处理方式见 §6.4。

### 6.2 修改 — 代码

| 文件 | 改动 | 关键行 |
|------|------|--------|
| `mini_agent/tools/__init__.py` | 移除 SessionNoteTool/RecallNoteTool 导入和 `__all__` 导出 | 6, 15-16 |
| `mini_agent/cli.py` | 移除 `from .note_tool import SessionNoteTool`、移除 `enable_note` 分支 | 39, 517-519 |
| `mini_agent/config.py` | 移除 `enable_note: bool = True`、移除 `tools_data.get("enable_note"...)` | 54, 167 |
| `mini_agent/tools/memory_manager.py` | `PROJECT_INSTRUCTION_FILENAME = "AGENTS.md"`、`GLOBAL_INSTRUCTION_FILE = ~/.mini-agent/AGENTS.md` | 27-28 |

### 6.3 修改 — 配置

| 文件 | 改动 |
|------|------|
| `mini_agent/config/mcp.json` | `memory.disabled`: `false` → `true` |
| `mini_agent/config/config-example.yaml` | 移除 `enable_note: true` 注释行（46 行） |
| `mini_agent/config/system_prompt.md` | 末尾追加 §5 的 Memory System 章节 |

### 6.4 修改 — 示例

两个示例文件处理方式不同（因 04 用多工具，仅 03 整文件围绕 SessionNoteTool）：

**`examples/03_session_notes.py`** — 顶部加 deprecation 注释，保留代码不动：

```python
# DEPRECATED: SessionNoteTool has been removed from Mini-Agent core.
# This example is preserved as a conceptual reference for pluggable
# storage backend patterns. The current memory system uses
# MemoryManager + Markdown files. See docs/memory-system-design.md.
```

已知遗留：此文件 import 已删的 `SessionNoteTool`，运行会失败。作为模式参考保留。

**`examples/04_full_agent.py`** — 移除 SessionNoteTool/RecallNoteTool 引用（约 85-86 行），保持其余工具可用。加一行注释：

```python
# SessionNoteTool removed — use MemoryManager + AGENTS.md instead.
```

### 6.5 修改 — 测试

| 文件 | 改动 | 关键行 |
|------|------|--------|
| `tests/test_memory.py` | 所有 `MINI_AGENT.md` → `AGENTS.md` | ~5 处 |
| `tests/test_session_integration.py` | 移除 SessionNoteTool 引用 | 40, 127-136 |
| `tests/test_integration.py` | 移除 SessionNoteTool/RecallNoteTool 引用 | 68-69, 166-167 |
| `tests/test_agent.py` | 检查并按需修改 SessionNoteTool 引用 | — |

**不新增测试**：MemoryManager 逻辑不变，仅文件名常量更改，现有 19 个测试覆盖足够。

### 6.6 修改 — 文档

| 文件 | 改动 |
|------|------|
| `CLAUDE.md` | 移除 SessionNoteTool 提及；`MINI_AGENT.md` → `AGENTS.md` |
| `docs/memory-system-design.md` | 全面更新：文件名、移除 SessionNoteTool 共存段落、更新对比表 |
| `docs/PRODUCTION_GUIDE.md` / `_CN.md` | 移除 SessionNoteTool 提及（Context Management 行） |
| `docs/DEVELOPMENT_GUIDE.md` / `_CN.md` | **3.3 节保留不动**（用户决策）；其他部分按需更新 |
| `docs/run_agent_analysis.md` | 移除 SessionNoteTool 提及 |
| `ARCHITECTURE_ANALYSIS.md` | 移除 SessionNoteTool 提及 |

### 6.7 检查 — 项目根目录

若项目根存在 `MINI_AGENT.md`，`git mv` 重命名为 `AGENTS.md`。

## 7. 迁移

### 7.1 无数据迁移

SessionNoteTool 采用硬删除策略，**不提供数据迁移脚本**。理由：
- JSON 格式与目标 Markdown 不兼容
- Mini-Agent 是演示项目，实际用户少
- 现有 `.agent_memory.json` 通常为空或极少内容

### 7.2 副作用处理

1. **`MINI_AGENT.md` 重命名提示**：
   - 项目根：若有 `MINI_AGENT.md`，`git mv` 改名（实现内做）
   - 用户全局 `~/.mini-agent/MINI_AGENT.md`：**不自动动用户家目录文件**。启动时检测到旧文件名存在则打印 warning：
     ```
     ⚠️  Detected ~/.mini-agent/MINI_AGENT.md. Please rename to AGENTS.md.
     ```

2. **`.agent_memory.json` 遗留**：
   - workspace 下可能有旧 SessionNoteTool 的 JSON 文件
   - **不自动删除**（用户数据）
   - 启动时检测到则打印提示：
     ```
     ℹ️  Found .agent_memory.json (legacy SessionNoteTool data). Safe to delete.
     ```

## 8. 范围外（明确不做）

| 项目 | 推迟到 | 理由 |
|------|--------|------|
| 每日日志层 `memory/YYYY-MM-DD.md` | B 档 | 需要新加载/检索逻辑 |
| 语义检索（向量 + BM25） | C 档 | 需嵌入模型 + SQLite 索引 |
| Auto memory flush（压缩前保存） | C 档 | 需要额外 LLM 调用 |
| Dreaming 整合机制 | C 档 | 工程复杂度高 |
| 全局 LLM 记忆（第四档） | 不做 | 业界无先例，跨项目污染风险 |
| `MemoryBackend` Protocol 抽象 | 不做 | YAGNI |
| 数据迁移脚本 | 不做 | 硬删除策略 |
| `~/.mini-agent/` 目录改名 | 不做 | 仅文件名标准化，避免范围扩张 |

## 9. 已知遗留问题（接受）

1. **`examples/03_session_notes.py` 运行失败** — import 已删的类。作为模式参考保留，顶部 deprecation 注释说明。
2. **`docs/DEVELOPMENT_GUIDE.md` 3.3 节引用已删类** — 用户决策保留，未来再单独清理。
3. **`.agent_memory.json` 遗留文件** — 不自动删除，启动时打印提示。

## 10. 验收标准

- [ ] `pytest tests/ -v` 全部通过
- [ ] 启动 `mini-agent`，看到 `✅ Loaded memory system (dir: ~/.mini-agent/projects/Mini-Agent_<hash>/memory)`
- [ ] system prompt 包含 Memory System 章节（150/100 行硬约束）
- [ ] MCP memory 未加载（`disabled: true` 生效）
- [ ] 项目根的 `MINI_AGENT.md`（若有）已重命名为 `AGENTS.md`
- [ ] `~/.mini-agent/MINI_AGENT.md`（若存在）启动时打印迁移提示
- [ ] workspace 下 `.agent_memory.json`（若存在）启动时打印清理提示

## 11. 风险与回滚

- **风险等级**：低（纯删除 + 重命名 + 文档更新，无新逻辑）
- **风险点**：
  - 测试中遗漏 SessionNoteTool 引用导致 collection error → 实现时全量搜索
  - 文档中遗漏 SessionNoteTool 提及 → 实现时 grep 全量扫描
- **回滚**：`git revert` 单次提交即可

## 12. 实现顺序建议（供 writing-plans 参考）

1. **代码删除**：note_tool.py、相关测试、cli.py/config.py/__init__.py 引用
2. **常量重命名**：memory_manager.py（MINI_AGENT.md → AGENTS.md）+ 测试同步
3. **配置变更**：mcp.json disabled、config-example.yaml、system_prompt.md 新增章节
4. **项目根文件**：`git mv MINI_AGENT.md AGENTS.md`（若有）
5. **遗留检测**：cli.py 加 MINI_AGENT.md / .agent_memory.json 检测提示
6. **文档更新**：CLAUDE.md、memory-system-design.md、PRODUCTION_GUIDE、run_agent_analysis、ARCHITECTURE_ANALYSIS
7. **示例 deprecation 注释**：examples/03、04
8. **运行测试**：`pytest tests/ -v` 全绿
9. **手动验证**：启动 agent，检查 system prompt、MCP 状态、遗留提示

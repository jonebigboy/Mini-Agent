# Session 管理系统 — 设计文档

**日期**: 2026-06-17
**状态**: 已批准（待实现）
**范围**: B 标准（JSONL 持久化 + resume/fork/continue + 30 天 TTL + 进行中提示）
**作者**: brainstorming session 产出

---

## 1. 目标

为 Mini-Agent 引入完整的 session（对话会话）管理系统，对齐 Claude Code 的会话语义，解决当前"对话历史纯内存、进程退出即丢失"的根本缺口。

**核心目标**：
- Append-only JSONL 持久化对话原文（永不丢失）
- `--continue` / `--resume <id>` / `--fork <id>` 三种恢复语义
- `/sessions` REPL 命令列出 / 选择历史 session
- `mini-agent session cat/tail/rm` 子命令（替代并删除现有 logger.py）
- 24h 内未结束 session 的启动提示
- 现有 memory 系统目录从 `hash8` 迁移到 `encoded-cwd`（与 Claude Code 对齐）

**非目标**（不在本 spec 范围）：
- 文件级 checkpointing（按时间戳回退工作区文件改动）— 单独立项
- `SessionStore` 抽象层（为跨机器同步留口子）— 真有跨机器需求再加
- `rename_session` / `tag_session` API — 当 session 数量真成为问题再加
- Agent 的 `search_sessions` 工具 — 职责清晰，session 纯用户维度
- 分布式文件系统 / 跨机器同步 — 业界共识是本地 JSONL 已足够，跨机器用 SessionStore 适配器（YAGNI）

## 2. 背景

### 2.1 当前现状（差距分析）

| 关注点 | 当前实现 | 持久化? |
|---|---|---|
| 对话历史 | `agent.py:80` `self.messages: list[Message]` 内存 | 否 |
| 摘要压缩 | `agent.py:192-273` token > 80k 触发，**替换原文** | 否 |
| 跨会话记忆 | `~/.mini-agent/projects/<name>_<hash8>/memory/` | 是（本地） |
| 每会话日志 | `~/.mini-agent/log/agent_run_YYYYMMDD_HHMMSS.log` | 是（30 天 TTL） |
| 正式 "session" 概念 | 无 — Agent 实例即 session，仅靠日志时间戳隐式标识 | — |
| Session ID | 隐式：日志文件名时间戳 | — |

**关键缺口**：对话历史无法在重启后存活。无 resume、无 session 列表、无备份。Memory 是 LLM 编辑的精炼总结，不是对话档案。

### 2.2 业界参考

| 工具 | 存储格式 | 恢复语义 |
|---|---|---|
| **Claude Code** | append-only JSONL，`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` | `--continue`（最近）/ `--resume <id>` / `fork` |
| Aider | Markdown `.aider.chat.history.md` | `/save` 重建状态 |
| Cline | VS Code globalStorage + checkpointing | 脆弱（issue #6900 在改） |
| Cursor | UUID-based，`agent --resume=<UUID>` | 有 subagent resume bug |
| Gemini CLI | `--resume` / `-r` flag + `/restore` checkpoint | 类 Claude Code |

**关键发现**：**没有一家用分布式文件系统**。所有主流工具的共识是 **append-only JSONL + 本地文件**。分布式 FS 仅在多用户 server 部署或 CI ephemeral containers 场景才有意义，Mini-Agent 不属于此类。

### 2.3 选定范围：B 标准

| 功能 | A (MVP) | **B (标准)** | C (完整) |
|---|---|---|---|
| JSONL 持久化 | ✅ | ✅ | ✅ |
| Session ID | ✅ | ✅ | ✅ |
| `--continue` / `--resume` | ✅ | ✅ | ✅ |
| `/sessions` 列表 | ✅ | ✅ | ✅ |
| **`--fork`** | ❌ | ✅ | ✅ |
| **列表带 preview** | ❌ | ✅ | ✅ |
| **30 天 TTL** | ❌ | ✅ | ✅ |
| **进行中 session 提示** | ❌ | ✅ | ✅ |
| rename/tag API | ❌ | ❌ | ✅ |
| SessionStore 抽象 | ❌ | ❌ | ✅ |
| 文件 checkpointing | ❌ | ❌ | ✅ |

## 3. 关键决策

| # | 决策点 | 选定方案 | 章节来源 |
|---|---|---|---|
| 1 | 范围 | B 标准 | ch0 |
| 2 | 存储位置 | `~/.mini-agent/projects/<encoded-cwd>/sessions/<sid>.jsonl`（与 memory 同根，统一目录标识） | Q1 |
| 3 | 目录标识迁移 | hash8 → encoded-cwd，一次性自动迁移 | Q1 |
| 4 | Logger 关系 | **合并**：AgentLogger → SessionWriter，加 `session cat/tail` 子命令 | Q2 |
| 5 | 摘要与原文关系 | **原文永不丢**（append-only），reader 提供 raw/compacted 双模式 | Q3 |
| 6 | 大 tool_result | 外置到 `tool_outputs/<sid>/<seq>.txt` + sha256 引用 | H1 |
| 7 | Agent 工具 | 不给 `search_sessions`（职责清晰） | H3 |
| 8 | 并发 continue | `fcntl.flock` 独占锁 + mtime stale 检测 | H5 |
| 9 | Session ID | UUID4（无时间戳前缀） | ch3 |
| 10 | Writer fsync | 每次 append 后 `os.fsync`（崩溃恢复基础） | ch3 |
| 11 | Fork 实现 | 物理 cp JSONL + 新 session_start（含 `forked_from`） | ch3 |
| 12 | TTL | 复用 logger 现有逻辑（30 天，不删 locked） | ch3 |
| 13 | Resume 时进程内状态 | 只 `messages` 重放，其他 fresh 初始化 | H2 |
| 14 | 进程内状态配置 | config 永远是真相，session 只是历史 | ch4 |
| 15 | Workspace 绑定 | 默认严格（同 cwd）；`--any-workspace` flag 跨 workspace + 警告 | 缺口 3 |
| 16 | 信号处理 | flag-only + 第二次 SIGINT 立即退出 | ch4 修订 |
| 17 | 24h 提示 | 用最后事件 ts；联动 flock；默认 c（continue） | Patch 3 |
| 18 | tail -f | 200ms 轮询 + 5s writer 健康探活 + 4 种退出条件 | Patch 4 |
| 19 | Reader 健壮性 | partial last line 截断不崩（critical 合同） | Patch 5 |
| 20 | Migration 性能 | Fast-path regex + marker file，新用户零成本 | Patch 7/8 |

## 4. 架构

### 4.1 文件 / 模块布局

```
mini_agent/
├── session/                          # 🆕 新模块
│   ├── __init__.py
│   ├── store.py                      # SessionStore: JSONL 读写、TTL、清理
│   ├── writer.py                     # SessionWriter: 替代 AgentLogger，只写 JSONL
│   ├── reader.py                     # SessionReader: 加载、重放、过滤
│   ├── locker.py                     # FileLock + stale 检测
│   ├── events.py                     # SessionEvent schema + ExternalRefBlock
│   ├── migrate.py                    # hash8 → encoded-cwd 迁移逻辑
│   └── cli.py                        # session cat/tail/list/rm 子命令
├── agent.py                          # 改：注入 SessionWriter，替换 logger
├── cli.py                            # 改：加 --continue/--resume/--fork/sessions 命令
├── logger.py                         # ❌ 删除
├── paths.py                          # 🆕 路径常量（encoded-cwd、目录解析）
└── tools/
    └── memory_manager.py             # 改：路径从 hash8 改成 encoded-cwd
```

### 4.2 数据目录布局

```
~/.mini-agent/
├── .projects-v2-migrated             # 🆕 Marker: 迁移已执行
├── migration.log                     # 🆕 迁移审计日志（永久保留）
├── projects/
│   └── <encoded-cwd>/                # 例：Users-jone-Desktop-codespace-Mini-Agent
│       ├── memory/                   # 从 hash8 迁移过来
│       │   ├── MEMORY.md
│       │   └── <topic>.md
│       ├── sessions/
│       │   ├── <session-id>.jsonl    # append-only 主文件
│       │   └── <session-id>.lock     # ad-hoc flock（进程死自动释放）
│       └── tool_outputs/
│           └── <session-id>/
│               └── seq-<N>.txt       # 大 tool_result 外置
└── log/                              # ❌ 删除（不读不写，让其自然过期）
```

### 4.3 encoded-cwd 编码规则

```python
# mini_agent/paths.py
def encode_cwd(cwd: str) -> str:
    """把绝对路径编码为目录名。/Users/jone/X → Users-jone-X"""
    return cwd.replace("/", "-").lstrip("-")

def project_dir(cwd: str) -> Path:
    return Path.home() / ".mini-agent" / "projects" / encode_cwd(cwd)

def sessions_dir(cwd: str) -> Path:
    return project_dir(cwd) / "sessions"

def tool_outputs_dir(cwd: str, sid: str) -> Path:
    return project_dir(cwd) / "tool_outputs" / sid

def memory_dir(cwd: str) -> Path:
    return project_dir(cwd) / "memory"
```

### 4.4 三大组件职责

| 组件 | 职责 |
|---|---|
| **SessionWriter** | Agent 执行时把每个事件 append 到 JSONL；管理 tool_outputs 外置；持有 flock |
| **SessionReader** | CLI 启动时读 JSONL；重放到 `Agent.messages`；提供 raw / compacted 双模式 |
| **SessionStore** | 目录管理：list / get / fork / cleanup；与具体读写解耦 |

### 4.5 与 Agent 类的衔接

`Agent.__init__` 改成接收 `SessionWriter`。所有 `self.logger.log_xxx()` 调用替换为 `self.session.append_xxx()`。`_create_summary` 触发时**额外**调用 `self.session.append_summary_event(...)` 记录元数据。

## 5. SessionEvent Schema

### 5.1 Event 类型表

每行 JSONL = 一个事件，统一 `type` 字段判别，**不**拆成 user_message / assistant_message 独立 type（复用 `Message.role`）：

| type | role | 用途 |
|---|---|---|
| `session_start` | — | session 创建，含 cwd/workspace/model/cli_args/forked_from/schema_version |
| `session_end` | — | 退出标记，含 end_reason |
| `message` | `user` / `assistant` / `tool` | 任何对话消息 |
| `summary_event` | — | 元数据，不影响 replay |
| `error` | — | 致命错误标记 |

### 5.2 SessionEvent 字段定义（含补丁）

```python
from pydantic import BaseModel
from typing import Literal, Any

class ExternalRefBlock(BaseModel):
    """严格类型化的 external_ref 块（新增字段，无现有消费方负担）。"""
    type: Literal["external_ref"] = "external_ref"
    path: str
    size: int
    sha256: str

class SessionEvent(BaseModel):
    """One line in a session JSONL file."""
    # 通用头
    seq: int
    ts: str                             # ISO 8601 with millis
    type: Literal[
        "session_start", "session_end", "message",
        "summary_event", "error",
    ]
    session_id: str                     # UUID4

    # message type
    role: str | None = None
    content: str | list[dict] | None = None   # text 块松散 dict，external_ref 块严格
    thinking: str | None = None
    tool_calls: list[dict] | None = None      # 内部 ToolCall.model_dump()，零格式转换
    tool_call_id: str | None = None
    name: str | None = None

    # session_start
    cwd: str | None = None
    workspace: str | None = None
    model: str | None = None
    cli_args: dict | None = None             # 已过滤 secrets
    schema_version: str | None = None        # 当前 "1"
    forked_from: str | None = None           # fork 时记录源 session_id

    # session_end
    end_reason: str | None = None

    # summary_event
    summarized_seqs: list[int] | None = None
    summary_seq: int | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None

    # error
    error: str | None = None
```

### 5.3 end_reason 枚举（含补丁）

**WRITTEN**（writer 实际写入）：
- `exit` — 正常退出（REPL `/exit` / EOF / `--task` 跑完）
- `interrupt` — 第一次 SIGINT
- `sigterm` — SIGTERM
- `switched` — REPL 内 `/resume` / `/fork` 软切换

**DERIVED**（reader 推断，不写入）：
- `crashed` — 缺 `session_end` AND flock 已释放

### 5.4 大 tool_result 外置机制

**阈值**：默认 **10 KB**，per-block 判定（不是整体）。

**写入规则**：
- writer 对 `content` 里的每个 text 块量 `len(text.encode("utf-8"))`
- 超 10 KB → 写入 `tool_outputs/<sid>/seq-<N>.txt`，content 块替换为 `ExternalRefBlock`
- 允许多 block 混合：`[{type:"text", text:"文件内容如下："}, {type:"external_ref", path:"...", size:..., sha256:"..."}]`

**Reader 重放规则**：
- 遇到 `external_ref` 块 → 读文件 + 校验 sha256 → 失败则替换为占位符 `"[tool_result unavailable: file missing or corrupted]"` + warn
- **不让整个 session 加载失败**

### 5.5 关键不变量

1. `summary_event` 是元数据，**不强制 reader 行为**。reader 提供 raw/compacted 双模式（见 6.3）。
2. **不存储 system prompt**（动态拼装，每次重算）。未来加 Anthropic prompt caching 时，MEMORY.md / AGENTS.md 改动 → cache miss，是预期行为。
3. `session_end` 缺失 = in-progress / crashed（靠 flock 区分）。
4. seq 单调递增、不重用。fork 时新 session 从 0 重新计数。
5. 文件名 = session_id（UUID），不含时间戳前缀。
6. 前向兼容：reader 遇到未知 `type` → warn + skip。

### 5.6 样例（一个完整的小 session）

```jsonl
{"seq":0,"ts":"2026-06-16T10:30:00.123Z","type":"session_start","session_id":"550e8400-...","cwd":"/Users/jone/.../Mini-Agent","workspace":"/Users/jone/.../Mini-Agent","model":"gpt-4o","cli_args":{"mode":"interactive"},"schema_version":"1"}
{"seq":1,"ts":"2026-06-16T10:30:01.456Z","type":"message","session_id":"550e8400-...","role":"user","content":"帮我看看 agent.py"}
{"seq":2,"ts":"2026-06-16T10:30:02.789Z","type":"message","session_id":"550e8400-...","role":"assistant","content":"我来读取 agent.py","tool_calls":[{"id":"call_1","type":"function","function":{"name":"file_read","arguments":"{\"path\":\"mini_agent/agent.py\"}"}}]}
{"seq":3,"ts":"2026-06-16T10:30:03.012Z","type":"message","session_id":"550e8400-...","role":"tool","tool_call_id":"call_1","name":"file_read","content":[{"type":"text","text":"文件内容如下："},{"type":"external_ref","path":"tool_outputs/550e8400-.../seq-3.txt","size":18234,"sha256":"a1b2c3..."}]}
{"seq":4,"ts":"2026-06-16T10:35:00.000Z","type":"summary_event","session_id":"550e8400-...","summarized_seqs":[2,3],"summary_seq":5,"tokens_before":85000,"tokens_after":12000}
{"seq":5,"ts":"2026-06-16T10:35:00.500Z","type":"message","session_id":"550e8400-...","role":"user","content":"[Assistant Execution Summary]\n\nRead agent.py, identified token logic at lines 192-273"}
{"seq":6,"ts":"2026-06-16T11:00:00.000Z","type":"session_end","session_id":"550e8400-...","end_reason":"exit"}
```

## 6. 生命周期 + Writer/Reader API

### 6.1 状态机

```
                    ┌──────────────────────┐
                    │   (进程启动)          │
                    └──────────┬───────────┘
                               │
                  ┌────────────▼────────────┐
                  │ CLI 解析 --continue/    │
                  │   --resume/--fork       │
                  └────────────┬────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                  │
       (default/         (--continue/        (--fork <id>)
        --resume new)      --resume <id>)
            │                  │                  │
            ▼                  ▼                  ▼
      [new session]    [acquire lock]      [copy source JSONL
       write start     load compacted       to new UUID file
       event           → Agent.messages     write start event
                                            with forked_from]
            │                  │                  │
            └──────────────────┴──────────────────┘
                               │
                  ┌────────────▼────────────┐
                  │   Agent 执行循环         │
                  │   writer.append_*() 每次 │
                  │   事件                   │
                  └────────────┬────────────┘
                               │
                  ┌────────────▼────────────┐
                  │ 退出（正常/Ctrl+C/crash)│
                  └────────────┬────────────┘
                               │
                  ┌────────────▼────────────┐
                  │ try/finally:             │
                  │   write session_end      │
                  │   release lock           │
                  └─────────────────────────┘
```

### 6.2 SessionWriter API

```python
class SessionWriter:
    """Append-only event writer. One per Agent instance."""

    def __init__(self, session_id: str, cwd: str, workspace: str,
                 model: str, cli_args: dict):
        self.session_id = session_id
        self._path = sessions_dir(cwd) / f"{session_id}.jsonl"
        self._tool_outputs = tool_outputs_dir(cwd, session_id)
        self._seq = -1
        self._lock = FileLock(f"{session_id}.lock")
        self._fh = None  # 常开

    # 生命周期
    def open(self) -> None:
        """Acquire flock, create file, write session_start. Secrets filtered."""
        # 1. Try acquire FileLock (fcntl.flock, exclusive, non-blocking)
        # 2. Lock held by live process → SessionLockedError
        # 3. Open JSONL for append, write session_start with schema_version="1"
        # 4. Redact secrets in cli_args (api_key/token/secret/password → "***")

    def close(self, end_reason: str = "exit") -> None:
        """Write session_end, release lock, close file handle."""
        # Always called from try/finally in cli.py

    # 事件追加
    def append_user_message(self, content) -> int: ...
    def append_assistant_message(self, content, thinking, tool_calls) -> int: ...
    def append_tool_result(self, tool_call_id, name, content) -> int: ...
    def append_summary_event(self, summarized_seqs, summary_seq,
                             tokens_before, tokens_after) -> int: ...
    def append_error(self, error: str) -> int: ...

    # 内部
    def _next_seq(self) -> int:
        """Monotonic. self._seq += 1; return self._seq. No allocate-without-write window."""

    def _externalize_if_large(self, content_block: dict, seq: int) -> dict:
        """If text block > 10 KB, write to tool_outputs/<sid>/seq-<N>.txt."""

    def _write_line(self, event: SessionEvent) -> int:
        """Serialize + os.fsync + append. Returns seq."""
        # CRITICAL: fsync after each line.
```

**Writer 不变量**：
1. 每次 `_write_line` 后 `os.fsync(fd)` — 崩溃恢复基础
2. `_next_seq` 和 `_write_line` 之间无 await / yield（同步），不会"分配未写"
3. flock 独占锁，进程崩溃自动释放
4. 文件 handle 在 session 生命周期内常开

**Agent 调用**（替换现有 logger）：

```python
# Before:
self.logger.log_request(messages, tools)
self.logger.log_response(content, thinking, tool_calls, finish_reason)
self.logger.log_tool_result(tool_name, arguments, result_success, ...)

# After:
self.session.append_user_message(user_input)
self.session.append_assistant_message(content, thinking, tool_calls)
self.session.append_tool_result(tool_call_id, tool_name, result_content)
```

### 6.3 SessionReader API（含 raw/compacted 双模式）

```python
class SessionReader:
    def __init__(self, cwd: str): ...

    def list_sessions(self) -> list[SessionMeta]:
        """Per session: id, started_at, ended_at (or None), message_count,
        last_user_message_preview, model, forked_from."""

    def load_messages(self, session_id: str,
                      mode: Literal["raw", "compacted"] = "compacted") -> list[Message]:
        """raw: audit/debug - replay all original messages
        compacted (default): resume - skip summarized_seqs, use summary_seq"""

    def get_meta(self, session_id: str) -> SessionMeta: ...
    def find_latest(self) -> SessionMeta | None: ...
    def find_in_progress(self, max_age_hours: int = 24) -> list[SessionMeta]:
        """Sessions with: no session_end AND flock acquirable AND last event ts < 24h."""
```

**Raw vs Compacted 实现**：

```python
def load_messages(self, session_id, mode="compacted"):
    events = self._read_all_events(session_id)

    if mode == "raw":
        return [e.to_message() for e in events if e.type == "message"]

    # compacted: 收集所有 summarized_seqs / summary_seqs
    summarized: set[int] = set()
    summary_seqs: set[int] = set()
    for e in events:
        if e.type == "summary_event":
            summarized.update(e.summarized_seqs)
            summary_seqs.add(e.summary_seq)

    return [
        e.to_message() for e in events
        if e.type == "message" and e.seq not in summarized
    ]
```

集合操作天然处理多层嵌套摘要（seq 5 被 sum，seq 10 又 sum seq 5）。

### 6.4 Fork 实现

```python
def fork_session(source_id: str, new_id: str, cwd: str) -> None:
    """Physical copy + new session_start with forked_from."""
    src = sessions_dir(cwd) / f"{source_id}.jsonl"
    dst = sessions_dir(cwd) / f"{new_id}.jsonl"
    shutil.copy2(src, dst)
    # append new session_start with forked_from=source_id
    # 后续事件继续 append 到 dst
```

物理复制简单、自包含、易删。代价是磁盘占用，但 Mini-Agent session 体积可控（典型 1-5 MB）。

### 6.5 TTL 清理

复用 `logger.py:_cleanup_old_logs` 逻辑，搬到 `SessionStore.cleanup_old_sessions()`：
- 30 天 TTL
- 永不删除当前 session（mtime 是当前）
- 永不删除持有有效 lock 的 session
- 同时清理 `tool_outputs/<sid>/`
- 单文件删除失败不阻塞其他文件

### 6.6 Resume 时进程内状态处理

| 状态 | resume 时 | fork 时 |
|---|---|---|
| `Agent.messages` | 从 JSONL compacted 加载 | 从 JSONL raw 加载（全量原文） |
| `Agent.system_prompt` | 重新构建（config + AGENTS.md + MEMORY.md） | 同 |
| `Agent.api_total_tokens` | **重置为 0** | 同 |
| `Agent.token_limit` / `model` | 用当前 config | 同 |
| `Agent.cancel_event` / `confirmation_request` | fresh 初始化 | 同 |
| `BackgroundShellManager` | **保持原状**（class-level singleton） | 同 |

**模型不一致警告**（Patch 6）：
```
⚠️ Session was created with model {old}, current is {new}.
   Loaded {N} messages from history ({M} compacted summaries generated under {old} — may be suboptimal for {new}).
   Consider /fork to start fresh, or continue with this history.
```

### 6.7 cli.py 启动分支（综合视图）

```python
def main():
    args = parse_args()
    cwd = os.getcwd()
    config = load_config()
    memory_manager = MemoryManager(cwd)

    # 通用初始化：无论 new/resume/fork 都跑
    memory_manager.initialize_memory()   # 加载 MEMORY.md / AGENTS.md → system prompt

    # Session 模式分支
    resume_id: str | None = None
    fork_id: str | None = None

    if args.continue:
        latest = SessionReader(cwd).find_latest()
        if not latest:
            sys.exit("No session to continue in current cwd.")
        resume_id = latest.id
    elif args.resume:
        resume_id = resolve_prefix(args.resume, cwd, any_workspace=args.any_workspace)
    elif args.fork:
        fork_id = resolve_prefix(args.fork, cwd)

    # 创建 writer + 加载 messages
    if resume_id:
        messages = SessionReader(cwd).load_messages(resume_id, mode="compacted")
        writer = SessionWriter.open_existing(resume_id, cwd=cwd, ...)
    elif fork_id:
        new_id = str(uuid.uuid4())
        fork_session(fork_id, new_id, cwd)
        messages = SessionReader(cwd).load_messages(fork_id, mode="raw")
        writer = SessionWriter.open_new(new_id, cwd=cwd, ..., forked_from=fork_id)
    else:
        new_id = str(uuid.uuid4())
        messages = []
        writer = SessionWriter.open_new(new_id, cwd=cwd, ...)

    agent = Agent(messages=messages, session=writer, ...)

    # 启动横幅
    print(f"🆔 Session {short_id(new_id)}")

    try:
        run_repl(agent, writer)
    except KeyboardInterrupt:
        pass
    finally:
        writer.close(end_reason=_shutdown_requested or "exit")
```

**关键点**：
- `initialize_memory()` 永远跑（Memory 与 Session 解耦）
- `SessionWriter.open_existing` vs `open_new`：前者 acquire lock + 不写 session_start；后者 acquire lock + 写 session_start（含 forked_from 可选）
- `resolve_prefix` 处理 `--resume abc123` 前缀匹配 + 歧义报错
- 信号 handler（7.8）设置在 `run_repl` 入口前

### 6.8 Reader 健壮性合同（CRITICAL）

```
读取 JSONL 时，任何单行 JSON parse 失败:
  - 该行截断到上一个 \n（丢弃 partial line）
  - warn: "session <id> has malformed line at byte offset N, truncated"
  - 继续读后续行（不 abort 整个 session 加载）

最常见 partial line 场景:
  - 进程在 f.write(line + "\n") 写到一半时崩溃
  - fsync 还没跑就断电（极罕见）

此合同保证: 任何 crash 都不会让 session 不可恢复。
```

## 7. CLI / UX

### 7.1 顶层命令接口

```bash
# 交互模式
mini-agent                              # 新 session（默认）
mini-agent --continue                   # 恢复当前 cwd 最近的 session
mini-agent --resume <session-id>        # 指定 session（支持前缀匹配）
mini-agent --fork <session-id>          # 从指定 session 分叉

# 一次性模式（保留）
mini-agent --task "..."

# Session 管理子命令（不进入 REPL）
mini-agent session list                 # 列出当前 cwd 的所有 session
mini-agent session cat <id>             # 完整 pretty-print
mini-agent session tail <id> [-f]       # 看末尾；-f 实时跟随
mini-agent session rm <id>              # 删除（连带 tool_outputs）
mini-agent migrate-orphans              # 手动处理 orphan 目录
```

**前缀匹配**：`--resume abc123` 等价于 `--resume abc12345-...`（前缀唯一时）。歧义时报错并列出候选。

**Workspace 绑定**：
- 默认严格：`--resume <id>` 只在当前 cwd 查找
- `--resume <id> --any-workspace`：全局搜索 `~/.mini-agent/projects/*/sessions/`，找到后警告 cwd 不匹配并要求确认

### 7.2 REPL slash 命令

| 命令 | 行为 |
|---|---|
| `/sessions` | 当前 cwd 的 session 列表 |
| `/resume [id]` | 无参 = 恢复最近；有参 = 指定 |
| `/fork [id]` | 从某 session 分叉 |
| `/session-id` | 打印当前 session_id |

**REPL 软切换状态语义**（Patch 修订）：
- `api_total_tokens` → 重置为 0
- `Agent.messages` → 替换为新 session 的 messages
- 旧 session 文件 → 写 `session_end` (reason="switched") + flush + close
- 新 session 文件 → 新 UUID + 新 SessionWriter
- `BackgroundShellManager` → **保持原状**（class-level singleton，跨 session 残留）。`/bg` 列表加一列 `started_in_session`（短 ID）让用户知道来源

### 7.3 启动时的 in-progress session 提示

无 timeout，默认 c（continue）。in-progress 判定联动 flock：

```
💡 检测到 1 个最近未结束的 session（24h 内）:

   [1] abc12345... | 2h ago | 12 msgs | "调试 MCP 加载失败的 bug"

   选序号 resume (1) / [n] new session / [f] fork
   > _
```

**in-progress 判定条件**：
1. 缺 `session_end` AND
2. flock 可获取（无活进程持有，确认是 crashed 而非 active）AND
3. 最后一条事件的 `ts` 在 24h 内（不是文件 mtime，避免 cp / chmod 干扰）

**探活-释放-重加锁竞态**（Patch 1）：
- 探活：non-blocking acquire + 立即 release
- 用户选择 resume → 真正 acquire
- 若失败 → "Session was taken by another process, please retry"，回到 `/sessions` 列表

### 7.4 Session ID 启动横幅

```
🆔 Session abc12345-6789-...
   Resume later:  mini-agent --resume abc12345
   Fork from it:  mini-agent --fork   abc12345
```

短前缀（前 8 字符）够 resume/fork 用。

### 7.5 `/sessions` / `session list` 输出格式

```
ID            Started              Status        Msgs  Preview
abc12345...   2026-06-16 14:23     ● in-prog     12    调试 MCP 加载失败的 bug
def67890...   2026-06-15 10:00     ✓ ended       42    加个 /init 命令
ghi13579...   2026-06-14 18:30     ✓ ended       28    重构 auth 模块用 JWT
              └─ forked from xyz98765
```

- **Status**：`● in-prog` / `✓ ended` / `⚠ stale-locked`（lock 存在但 mtime > 1h）
- **Msgs**：message 事件计数（不含 summary_event 等）
- **Preview**：**最后一条 user 消息**的前 40 字符
- 排序：按 `started_at` 倒序
- 分页：默认 20 条，`--all` 看全部

### 7.6 `session cat <id>` 输出格式

```
Session: abc12345-...
Started: 2026-06-16 14:23:00
Ended:   2026-06-16 16:00:42  (end_reason: exit)
Model:   gpt-4o
Cwd:     /Users/jone/.../Mini-Agent

────────────────────────────────────────────────────────────────
[seq 1] USER                                    14:23:01.456
  帮我看看 agent.py 的 token 逻辑

[seq 2] ASSISTANT                               14:23:02.789
  我来读取 agent.py
  thinking: 用户想了解 token 管理，先读文件
  tool_calls:
    [call_1] file_read({"path": "mini_agent/agent.py"})

[seq 3] TOOL  file_read                         14:23:03.012
  [external_ref → tool_outputs/abc12345.../seq-3.txt]
  size: 18,234 bytes  sha256: a1b2c3...

[seq 4] SUMMARY_EVENT                           14:35:00.000
  summarized: [2, 3]   →  summary at seq 5
  tokens: 85,000 → 12,000
```

CLI flags：
- `--format text|json`（默认 text，json 输出原始 JSONL 流，便于管道）
- `--expand-external`：把所有 external_ref 拉出来 inline 显示

### 7.7 `session tail <id> [-f]` 行为（Patch 4 修订）

- 默认：最后 20 条事件
- `-n <count>`：最后 N 条
- `-f`：阻塞等待新事件，200ms 轮询（优先 FSEvents / inotify，失败回退 polling）

**终止条件**：
1. 观察到 `session_end` 事件 → exit 0
2. 文件被 unlink（`rm` 子命令并发）→ exit + warn
3. Ctrl+C → 退出
4. Writer 死但无 session_end（flock 探活 5s 一次）→ "writer seems crashed, tail stopped" + exit

**视图差异文档化**：`session tail` 输出的是**结构化 JSONL 事件**（pretty-print），不是 agent 在另一个 terminal 看到的渲染后 markdown。同一份事实但不同视图。

### 7.8 信号处理（Patch 修订）

```python
_shutdown_requested: str | None = None

def _sig_handler(signum, frame):
    global _shutdown_requested
    if _shutdown_requested is not None:
        # 第二次信号 → 立即退出
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        raise KeyboardInterrupt
    _shutdown_requested = "interrupt" if signum == signal.SIGINT else "sigterm"
    # flag-only, no I/O

signal.signal(signal.SIGINT, _sig_handler)
signal.signal(signal.SIGTERM, _sig_handler)
```

主循环：

```python
while agent.has_more_turns():
    if _shutdown_requested:
        break
    await agent.run_one_turn()

try: pass
finally:
    writer.close(end_reason=_shutdown_requested or "exit")
```

**信号安全合同**：
- Handler 只做：赋值 / `signal.signal` 恢复 / `raise KeyboardInterrupt`
- Handler 绝不做：asyncio / I/O / 锁
- SIGKILL / OOM 仍丢 session_end → reader 视为 crashed

### 7.9 错误场景 CLI 输出

| 场景 | 输出 |
|---|---|
| `--resume <bad-id>` 找不到 | stderr: `Error: session "xxx" not found.` + 提示 `session list` |
| 前缀歧义 | stderr: `Error: ambiguous prefix "ab", matches: [...]` |
| Lock 被活进程持有 | stderr: `Error: session "xxx" is being used. Use --fork to branch.` |
| 加载时 JSON 残缺 | stdout: `[warn] session abc has malformed line at seq 42, skipping` |

错误走 stderr，正常输出走 stdout，方便管道。

## 8. Migration：hash8 → encoded-cwd

### 8.1 触发时机

首次启动新版本时自动触发。不写单独的 `migrate` 命令（用户不该需要手动跑）。

### 8.2 Fast-path Skip（Patch 7）

```python
def has_legacy_projects() -> bool:
    projects = Path.home() / ".mini-agent" / "projects"
    if not projects.exists():
        return False
    legacy_pattern = re.compile(r"_[0-9a-f]{8}$")
    return any(legacy_pattern.search(p.name)
               for p in projects.iterdir() if p.is_dir())

if not has_legacy_projects():
    return  # 零成本退出
```

**已知边界**：encoded-cwd 路径若碰巧以 `_<8-hex>` 结尾（如 `/path/to/foo_a1234567`）会假阳性。处理：orphan 兜底逻辑判断 "orphan dir 内容空 + `<name>` 部分含多个 `-` 段（像 encoded-cwd）→ 静默跳过，不写 orphan 警告"。

### 8.3 Marker File（Patch 8）

`~/.mini-agent/.projects-v2-migrated` 作为 "迁移逻辑已执行" 证据：

```
- Marker 存在 → 完全跳过 fast-path + 扫描（最常见路径，O(1)）
- Marker 不存在 → 跑一次完整迁移，跑完创建 marker
- MINI_AGENT_FORCE_MIGRATE=1 → 忽略 marker 强制重扫（debug/orphan 处理）
- MINI_AGENT_NO_MIGRATE=1 → 完全跳过
```

新用户首次启动：fast-path 返回 False → 创建 marker → 后续启动直接跳过整段迁移代码。

### 8.4 反查 hash8 → cwd 的搜索策略（Patch 9）

hash8 算法：`hashlib.sha256(str(path).encode()).hexdigest()[:8]`，name 是 basename。可反向验证。

```
1. cwd fast-path（毫秒级）:
   计算 hash8(str(cwd))，与 ~/.mini-agent/projects/<basename>_<hash8> 比对
   命中 → 直接迁移，跳过其他扫描

2. macOS Spotlight 优先（mdfind -name <basename>，毫秒级）
   Linux locate 优先（locate <basename>，毫秒级）
   拿到候选列表 → hash8 验证 → 唯一匹配自动迁移

3. 文件系统遍历兜底（限 maxdepth=6, 排除 node_modules/.git，5s 超时）
```

`Path.cwd().parent` **不**作为搜索根（无特殊地位）。

### 8.5 迁移操作

```python
def migrate(old_dir: Path, new_path: Path):
    new_dir = project_dir(str(new_path)) / "memory"

    if new_dir.exists():
        print(f"⚠️ Skipping {old_dir.name}: target {new_dir} already exists")
        return

    new_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_dir), str(new_dir))   # 同 fs 原子，跨 fs copy+unlink
    print(f"✓ Migrated {old_dir.name} → {new_dir.parent.name}/")
```

**关键**：
- `shutil.move` 失败时 old_dir 保持完整（同 fs 是 rename(2) 原子）
- 跨文件系统（Patch B）：退化为 copy + unlink，可能留 partial state。**假设** `~/.mini-agent` 与用户项目同 fs（绝大多数情况成立）。跨 fs 失败时手动清理 new_dir。
- 新目录已存在 → 跳过 + 不覆盖

### 8.6 三种匹配结果

| 场景 | 处理 |
|---|---|
| 唯一匹配 | 自动迁移 + 日志 |
| 多匹配 | 交互式询问（用户输入路径） |
| 零匹配 | **保留原目录不删**，标记为 orphan |

**Orphan 保留**重要：用户可能在外部盘有项目，扫描找不到。删了就是数据丢失。

### 8.7 Migration Log（Patch 12）

`~/.mini-agent/migration.log` 永久保留（不删，文件极小）：

```
2026-06-17T10:30:00 INFO Migrated Mini-Agent_a3f8c1b2 → Users-jone-Desktop-codespace-Mini-Agent
2026-06-17T10:30:00 WARN Orphan: old-proj_c1a4f5cd (no matching path found)
```

主要作用：**审计**（"什么时候迁了什么"），不是性能优化路径。性能靠 marker file。

### 8.8 老日志目录处理

`~/.mini-agent/log/*.log`：
- 不迁移（JSONL 格式不一样）
- 不删除（让现有 30 天 TTL 自然过期）
- 新版本完全不读不写这个目录
- 30 天后自动清空

文档说明："老日志保留 30 天后自动清除，如需长期保存请手动备份"。

### 8.9 Hash Collision 文档化（Patch 11）

`hash8 = sha256(path)[:8]` = 32 bits。Birthday 碰撞：
- 100 项目：~1e-6
- 1000 项目：~1e-4

对个人使用可接受。`basename + hash8` 联合作为唯一键。**encoded-cwd 不存在碰撞风险**（字面路径），新版本之后此问题永久消失。hash8 只是历史遗留。

### 8.10 版本兼容性

| 用户场景 | 行为 |
|---|---|
| 老用户首次启动新版本 | 触发迁移（8.2-8.7） |
| 老用户回退到老版本 | 老版本找不到 hash8 → 创建新空 memory（用户体验降级但安全） |
| 新用户首次启动 | 无旧目录，直接用新格式 |
| 两版本间切换 | Marker 在 → 不重扫；老版本可能创建空 hash8 目录 → 留给 `migrate-orphans` 手动处理 |

## 9. 错误处理总览

### 9.1 错误处理矩阵

| 组件 | 错误场景 | 处理 | 来源 |
|---|---|---|---|
| SessionWriter.open | lock 被活进程持有 | `SessionLockedError` → CLI 提示 `--fork` | ch3 |
| | 路径不可写 | 退出 + 明确错误信息 | ch3 |
| SessionWriter.append | 磁盘满 | 抛错 → cli.py finally 仍写 session_end | ch3 |
| | 写一半进程崩 | 最多丢最后一条事件（fsync + append-only） | ch3 |
| SessionReader.load | 最后一行 JSON 残缺 | **截断 + warn，不崩**（critical 合同） | Patch 5 |
| | external_ref 文件丢失 | 替换占位符 + warn | ch2 |
| | external_ref sha256 不匹配 | 同上 | ch3 |
| | 未知 event type | warn + skip（前向兼容） | ch2 |
| | Session 文件不存在 | `SessionNotFoundError` → CLI 提示 | ch3 |
| | 探活后重加锁失败 | "Session was taken, retry" | Patch 1 |
| CLI / --resume | bad session ID | "not found" 错误 | ch4 |
| | 前缀歧义 | 列出候选 + 报错 | ch4 |
| | 跨 workspace（默认严格） | "not found" + 提示 `--any-workspace` | 缺口 3 |
| | `--any-workspace` 跨 workspace | 警告 cwd 不匹配，要求确认 | 缺口 3 |
| | 模型不一致 | warn（含 compacted 历史质量提示） | Patch 6 |
| CLI / REPL 软切换 | api_total_tokens | 重置为 0 | ch4 |
| | BackgroundShellManager | 不动（加 started_in_session 列） | ch4 |
| CLI / 信号 | 第一次 SIGINT | flag-only | 7.8 |
| | 第二次 SIGINT | 立即退出 | 7.8 |
| | SIGTERM | 同第一次 SIGINT | 7.8 |
| | SIGKILL / OOM | session_end 缺失 → reader 视为 crashed | ch2 |
| CLI / tail -f | 见 session_end | exit 0 | Patch 4 |
| | 文件 unlink | exit + warn | Patch 4 |
| | 第二次 Ctrl+C | 立即退出 | Patch (ch4 修订) |
| | writer 死但无 session_end | "writer seems crashed" + exit | Patch 4 |
| Migration | 单匹配 | 自动迁移 | ch5 |
| | 多匹配 | 交互式询问 | ch5 |
| | 零匹配 | orphan 保留 | ch5 |
| | 新目录已存在 | 跳过 + 不覆盖 | ch5 |
| | shutil.move 失败 | old_dir 不动（同 fs 原子；跨 fs 可能 partial） | Patch B |
| | marker 存在 | 完全跳过 | Patch 8 |
| | MINI_AGENT_NO_MIGRATE=1 | 完全跳过 | ch5 |
| | MINI_AGENT_FORCE_MIGRATE=1 | 忽略 marker | Patch 8 |

### 9.2 错误传播原则

1. **进程内错误**（SessionLockedError 等）：抛异常，cli.py 顶层 try/except 转 stderr + exit 1
2. **数据完整性错误**（partial line 等）：reader 内部处理，**绝不向 Agent 传播**
3. **审计类 warn**：stdout（不阻塞），可 grep
4. **致命错误**：stderr + exit 非 0，让 shell pipe 检测

## 10. 测试策略

### 10.1 测试金字塔

```
                    ┌────────────────┐
                    │ e2e (5%)        │  完整 CLI 跑 session + resume + fork
                    └────────────────┘
                ┌──────────────────────┐
                │ integration (20%)    │  Agent + SessionWriter 端到端跑一个 turn
                └──────────────────────┘
            ┌──────────────────────────────┐
            │ unit tests (75%)             │  每个 class / function
            └──────────────────────────────┘
```

### 10.2 必须覆盖的单元测试

**SessionWriter**：
- 正常 append 路径
- seq 单调性（无 gap）
- 每次 append 后文件可被 reader 读出（fsync 有效）
- 大 tool_result 外置 + sha256 正确
- 多 block 混合（text + external_ref）
- cli_args 泄密过滤（api_key/token/secret 替换 ***）
- secret 边界（deeply nested、大小写变体）

**SessionReader**：
- raw 模式重放（全部 message）
- compacted 模式（跳过 summarized_seqs）
- 多层 summary_event 嵌套
- partial last line 截断不崩（critical）
- external_ref 文件丢失 → 占位符 + warn
- external_ref sha256 不匹配 → 占位符 + warn
- 未知 event type → warn + skip
- 多 session 并存（不混淆）

**FileLock**：
- 活进程持有 → 第二个进程失败
- 进程死亡 → flock 自动释放
- stale lock 探活后释放 + 立即重加锁
- 探活-释放-重加锁竞态 → 报错让用户重试

**Migration**：
- 单匹配 / 多匹配 / 零匹配三种结果
- 新目录已存在 → 不覆盖
- shutil.move 失败 → old_dir 完整（mock 跨 fs EXDEV）
- marker file 存在 → 跳过
- MINI_AGENT_NO_MIGRATE / MINI_AGENT_FORCE_MIGRATE 环境变量
- regex false positive（`/path/to/foo_a1234567` 编码后误匹配）→ orphan 静默
- 老用户回退再切回 → marker 在 → 不重扫

**SessionFork**：
- 物理 cp 完整
- session_start 写 forked_from
- 原 session 不被修改
- fork 后 fork 路径独立

### 10.3 必须覆盖的集成测试

- 完整 Agent turn：user msg → assistant msg → tool call → tool result → 落 JSONL → reader 重放
- compacted resume：触发摘要 → 退出 → resume → compacted 加载 → 不重复摘要
- raw resume：同上但 raw 模式 → 全量重放 → 立即触发新摘要
- fork 后两边独立演化

### 10.4 必须覆盖的 e2e

- `mini-agent --continue` 恢复最近 session
- `mini-agent --resume <prefix>` 前缀匹配
- `mini-agent --fork <id>` 创建分叉
- `mini-agent session cat <id>` pretty-print 输出格式
- `mini-agent session tail <id> -f` 实时跟随（用 mock writer 慢速写）
- 信号测试：启动 → SIGINT → session_end written → 退出
- 第二次 SIGINT 测试：立即退出不等 graceful

### 10.5 性能基线（纳入 CI）

- 单条 event append 延迟 < 5ms（含 fsync）
- 1000 event session 的 `reader.load_messages` < 200ms
- migration fast-path skip < 5ms（marker 存在或无 legacy）
- 完整 migration 100 个 legacy dirs < 10s

## 11. 实施次序建议

1. **paths.py + encoded-cwd**：基础路径工具，其他模块依赖
2. **session/events.py + ExternalRefBlock**：schema 定义
3. **session/locker.py**：flock 包装
4. **session/writer.py**：替换 logger
5. **session/reader.py**：含 raw/compacted 双模式 + partial line 截断
6. **session/store.py**：目录管理 + TTL 清理
7. **session/migrate.py**：hash8 迁移
8. **agent.py 改造**：注入 SessionWriter，`_create_summary` 加 summary_event
9. **cli.py 改造**：加 flags、slash 命令、信号 handler、in-progress 提示
10. **session/cli.py**：session cat/tail/list/rm 子命令
11. **删除 logger.py + 移除 /log 命令**
12. **测试**：unit → integration → e2e

## 12. 性能与资源预算

- 单 session JSONL 体积：典型 1-5 MB（200 turn）
- fsync 开销：~1-3ms per append（SSD）
- 1000 个 session 占用：~1-5 GB（30 天 TTL 自然控制）
- tool_outputs 单 session：< 100 MB（10 KB 阈值下，大输出占比小）
- migration 一次性开销：< 10s（100 个 legacy dirs）
- 启动 fast-path：< 5ms（marker 或无 legacy）

## 13. 后续演进路径（不在本 spec）

- **C 完整范围**：rename/tag API + SessionStore 抽象 + 文件 checkpointing
- **跨机器同步**：实现 `SessionStore` 适配器，mirror JSONL 到 S3 / 分布式 FS
- **Agent 工具**：当 agent 真有"查过去决策"需求时再加 `search_sessions`（届时配套语义检索）
- **system_prompt caching**：Anthropic prompt caching 集成（届时 session_start 加 `system_prompt_sha256`）

---

## 附录 A：与 Claude Code 的对齐情况

| 维度 | Claude Code | Mini-Agent 本 spec |
|---|---|---|
| 存储格式 | append-only JSONL | ✅ 一致 |
| 路径 | `~/.claude/projects/<encoded-cwd>/<sid>.jsonl` | ✅ 一致（`~/.mini-agent/...`） |
| Session ID | UUID4 | ✅ 一致 |
| `--continue` | 恢复最近 | ✅ 一致 |
| `--resume <id>` | 按 ID | ✅ 一致（含前缀匹配） |
| `fork` | 物理复制 + forked_from | ✅ 一致 |
| raw vs compacted | 默认 compacted | ✅ 一致 |
| `session cat/tail` | 提供 | ✅ 一致 |
| 跨机器 | `SessionStore` 适配器 | ❌ 推迟（YAGNI） |
| 文件 checkpointing | 提供 | ❌ 推迟（独立复杂度） |

## 附录 B：累计 Patch 清单

| # | Patch | 落地章节 |
|---|---|---|
| 1 | 探活-释放-重加锁的竞态 → 报错让用户重试 | 7.3 |
| 2 | end_reason 枚举（WRITTEN vs DERIVED 澄清） | 5.3 |
| 3 | 24h 用最后事件 ts 而非 mtime | 7.3 |
| 4 | tail -f 四种终止条件 + 5s writer 健康探活 | 7.7 |
| 5 | partial last line 必截断不崩（critical 合同） | 6.8 |
| 6 | 模型不一致 compacted warn 文案 | 6.6 |
| 7 | Fast-path skip（regex 检测 legacy） | 8.2 |
| 8 | Marker file `.projects-v2-migrated` | 8.3 |
| 9 | 搜索根改 cwd 优先 + macOS Spotlight / Linux locate 优先 | 8.4 |
| 10 | Cross-fs shutil.move 非原子文档化 | 8.5 |
| 11 | Hash collision 文档化 | 8.9 |
| 12 | migration.log + marker 防重复迁移 | 8.7 |

# Session 管理系统 — 实现回顾与改进建议

**日期**: 2026-06-19
**状态**: 已实现（commit `805cb2b`，单次 squash 合并自 20 个增量提交）
**范围**: 37 files changed, +9511 / -628 lines
**前置文档**:
- 设计 spec：`docs/superpowers/specs/2026-06-17-session-management-design.md`
- 实施计划（EN）：`docs/superpowers/plans/2026-06-18-session-management.md`
- 实施计划（中文）：`docs/superpowers/plans/2026-06-18-session-management.zh.md`

本文档对实际落地的代码做完整回顾：每一处修改的理由、当前已知问题、后续可改进的方向。

---

## 1. 实现总览

### 1.1 一句话总结

把"对话历史纯内存、进程退出即丢失"的 Agent 改造成"每个会话追加式写入 JSONL，支持 `--continue` / `--resume` / `--fork` 三种恢复语义，并对齐 Claude Code 的 `~/.claude/projects/<encoded-cwd>/` 路径布局"。

### 1.2 核心数据流

```
用户输入
  │
  ▼
Agent.add_user_message(content)
  │   └─→ self.messages.append(...)            # 内存
  │   └─→ self.session.append_user_message()   # JSONL 追加（fsync）
  │   └─→ self._message_seqs.append(seq)       # 平行索引
  ▼
Agent.run() → LLM call → assistant_msg
  │   └─→ self.session.append_assistant_message(...)
  ▼
Tool execution → tool_msg
  │   └─→ self.session.append_tool_result(...)
  ▼
Token 超限 → _summarize_messages()
  │   └─→ self.session.append_user_message(summary_text)
  │   └─→ self.session.append_summary_event(summarized_seqs, summary_seq, ...)
  ▼
用户 /exit 或 SIGINT → run_repl 退出
  │
  ▼
run_agent finally → session_writer.close(end_reason)
```

JSONL 落盘位置：`~/.mini-agent/projects/<encoded-cwd>/sessions/<session_id>.jsonl`
其中 `encode_cwd("/Users/jone/code/mini-agent")` → `"Users-jone-code-mini-agent"`。

---

## 2. 文件级修改清单与理由

### 2.1 新增：`mini_agent/paths.py`（44 行）

**职责**：encoded-cwd 路径工具集。

```python
encode_cwd("/Users/jone/X")  → "Users-jone-X"
project_dir(cwd)             → ~/.mini-agent/projects/<encoded-cwd>/
sessions_dir(cwd)            → .../sessions/
tool_outputs_dir(cwd, sid)   → .../tool_outputs/<sid>/
memory_dir(cwd)              → .../memory/
migration_marker_path()      → ~/.mini-agent/.projects-v2-migrated
migration_log_path()         → ~/.mini-agent/migration.log
```

**理由**：
- 替代旧的 `<name>_<hash8>` 方案。`hash8` 在项目改名/移动后失效；`encoded-cwd` 可读、可逆、跨机器一致（对齐 Claude Code）。
- 集中管理避免 `~/.mini-agent/projects/...` 字符串散落在多个模块。
- `migration_marker_path` 让一次性迁移可幂等：marker 存在即跳过。

### 2.2 新增：`mini_agent/session/events.py`（71 行）

**职责**：Pydantic schema。

```python
class ExternalRefBlock:
    type: Literal["external_ref"]
    path: str           # 相对 tool_outputs/<sid>/ 的路径
    size: int           # 字节数
    sha256: str         # hex digest（min=max=64）

class SessionEvent:
    seq: int                      # 0-indexed，单调递增
    ts: str                        # ISO8601
    type: Literal[
        "session_start",
        "session_end",
        "message",
        "summary_event",
        "error",
    ]
    session_id: str
    # type 特定字段：role/content/thinking/tool_calls/end_reason/
    #               summarized_seqs/summary_seq/tokens_before/tokens_after/...
```

**理由**：
- discriminated union 模式：`type` 决定哪些字段是必填的，Pydantic 自动校验。
- `ExternalRefBlock` 让 tool_result 不论多大都不会撑爆 JSONL；`sha256` 校验防止磁盘损坏静默通过。
- `summary_event` 是**纯元数据**（被摘要的 seq 范围 + 摘要消息所在的 seq），不影响 replay，只影响 `compacted` 模式的读取策略。

### 2.3 新增：`mini_agent/session/locker.py`（58 行）

**职责**：`fcntl.flock` 包装。

```python
class FileLock:
    def acquire(self, blocking=True) -> bool
    def release(self) -> None
    def probe(self) -> bool           # 非阻塞尝试 + 立即释放
    def __enter__/__exit__

class SessionLockedError(Exception): ...
```

**理由**：
- POSIX `fcntl.flock` 在进程死亡时**内核自动释放**，不需要 PID 文件、不需要 stale lock 检测。
- `probe()` 用于 TTL 清理和 `find_in_progress`：探测锁是否被持有，本身不持有。
- 跨平台限制：Windows 不支持 `fcntl`，本模块当前只在 macOS/Linux 工作（与原设计一致）。

### 2.4 新增：`mini_agent/session/writer.py`（254 行）

**职责**：append-only JSONL writer，每个 Agent 一个实例。

```python
class SessionWriter:
    def __init__(self, session_id, cwd, workspace, model, cli_args): ...
    def open(self, forked_from=None) -> None
    def close(self, end_reason="exit") -> None
    def append_user_message(content) -> int          # 返回 seq
    def append_assistant_message(content, thinking, tool_calls) -> int
    def append_tool_result(tool_call_id, name, content) -> int
    def append_summary_event(summarized_seqs, summary_seq, ...) -> int
    def append_error(error) -> int
```

**关键设计**：
- `_redact_secrets`：正则 `api[_-]?key|token|secret|password|credential|auth`（IGNORECASE）匹配的 value 替换为 `[REDACTED]`。**防止 API key 泄漏到 JSONL**。
- `_externalize_content`：单条 text 块 > 10 KB 时落盘到 `tool_outputs/<sid>/seq-N.txt`，event 里只放 `ExternalRefBlock`（path/size/sha256）。
- `_append_event` 每次都 `os.fsync(fileno())`，**保证崩溃时不丢失超过最后一行**。代价是写入吞吐（约 1-2 kHz），但 Agent 每步最多写 5-10 个事件，远低于上限。
- `_seq` 从 -1 起步，`_next_seq` 先自增再返回（首事件 seq=0）。
- `open()` 用 `"a"` 模式：resume 时不会覆盖已有内容。

### 2.5 新增：`mini_agent/session/reader.py`（173 行）

**职责**：读 JSONL，两种模式。

```python
class SessionReader:
    def iter_events(self, session_id) -> Iterator[SessionEvent]
    def load_messages(self, session_id, mode="raw"|"compacted") -> list[Message]
```

**两种模式**：
- `raw`：审计视图，所有事件按顺序转成 `Message`。包含被摘要的原始消息。
- `compacted`：resume 视图，**跳过所有 `summary_event.summarized_seqs` 引用的 seq**，只保留摘要消息和摘要范围外的内容。Token 占用更小。

**鲁棒性契约**（生产代码必须假设磁盘会坏）：
- 半行（JSON 解析失败）→ 截断该行，`warnings.warn`，继续。
- 未知 `type` 值（未来版本写入）→ warn + skip，不崩。
- `external_ref` 文件丢失或 sha256 不匹配 → 用占位符 `[external_ref missing: ...]` 替换，不崩。

**关键实现细节 — `_coerce_tool_calls`**：
```python
@staticmethod
def _coerce_tool_calls(raw, session_id, seq) -> list[ToolCall] | None:
    # SessionEvent.tool_calls 是宽松的 list[dict]
    # Message.tool_calls 要求 list[ToolCall]（Pydantic 模型）
    # 这里逐个 model_validate，失败的 warn + skip
```
**理由**：SessionEvent 故意保持 schema 宽松（接受任意 dict，方便未来字段扩展），但 Message schema 严格。读取时必须做一层验证转换，不能假设输入是干净的。

### 2.6 新增：`mini_agent/session/store.py`（185 行）

**职责**：session 列表、查找、fork、TTL。

```python
@dataclass
class SessionMeta:
    session_id, started_at, last_event_at, ended, end_reason,
    message_count, preview, model, forked_from

class SessionStore:
    def list_sessions() -> list[SessionMeta]
    def find_latest() -> SessionMeta | None           # 给 --continue 用
    def find_in_progress(max_age_hours=24) -> list[SessionMeta]
    def fork_session(source_sid, new_sid) -> None     # shutil.copy2
    def cleanup_old_sessions(max_age_days=30) -> int
    def session_path(sid) -> Path
    def _derive_meta(path) -> SessionMeta              # 读前 200 事件派生
```

**关键设计**：
- `find_in_progress` 用 `FileLock.probe()`：锁仍被持有 = 有进程在写。但崩溃的进程会被内核释放锁，所以"in-progress"的实际语义是"未写 session_end"。24h 窗口防止无限累积。
- `cleanup_old_sessions` **跳过被锁定的 session**（不能删正在写的）。
- `fork_session` 用 `shutil.copy2`（保留元数据）；同文件系统是原子 rename，跨文件系统退化为 copy+unlink。

### 2.7 新增：`mini_agent/session/migrate.py`（259 行）

**职责**：一次性把旧的 `<name>_<hash8>` 目录迁移到 `<encoded-cwd>`。

```python
def migrate_all(cwd: str) -> tuple[int, int]   # (migrated, orphans)
```

**查找策略**（按成本递增）：
1. cwd 快路径：直接看 `~/.mini-agent/projects/<encoded-cwd>/` 是否已有 marker。
2. marker 存在 → 整体跳过，零成本。
3. macOS：`mdfind`（Spotlight）按 `<name>_*` glob 查。
4. Linux：`locate`（如果装了）。
5. 兜底：`~/.mini-agent/projects/` 目录扫描（5 秒预算）。

**误报过滤**：`_looks_like_false_positive`：≥2 个连字符 + memory 目录为空 = 大概率不是本项目的旧目录。

**幂等性**：marker 文件 `~/.mini-agent/.projects-v2-migrated` 写入后，下次启动直接跳过整个 `migrate_all`。
**环境变量**：
- `MINI_AGENT_NO_MIGRATE=1`：完全跳过（CI 用）。
- `MINI_AGENT_FORCE_MIGRATE=1`：忽略 marker 强制重跑（调试用）。

### 2.8 新增：`mini_agent/session/cli.py`（218 行）

**职责**：`mini-agent session <list|cat|tail|rm>` 子命令处理器。

| 子命令 | 功能 |
|---|---|
| `list` | 表格输出 session 列表（id、started、status、msgs、preview），最多 20 条 |
| `cat <id>` | 默认 text 格式（按 seq 分段），`--format json` 输出原始 JSONL，`--expand-external` 展开 external_ref |
| `tail <id>` | 打印最后 N 条事件，`-f` follow 模式（200 ms 轮询，5 秒健康检查 writer liveness） |
| `rm <id>` | 删除 JSONL + tool_outputs 目录 |

### 2.9 修改：`mini_agent/agent.py`（+109 / -27）

**变更点**：
1. 删除 `from .logger import AgentLogger`，加 `TYPE_CHECKING` 导入 `SessionWriter`。
2. 构造器新增必填参数 `session_writer: "SessionWriter | None"`，`None` 抛 `ValueError`。
3. 新增 `self._message_seqs: list[int]`，平行于 `self.messages[1:]`（system prompt 不入 seq 列表）。
4. `add_user_message` / `run()` / tool 执行：每次写入 `self.messages` 后调用 `self.session.append_*()` 并把返回的 seq 追加到 `_message_seqs`。
5. `_summarize_messages`：summarize 后写 `summary_event`（summarized_seqs + summary_seq + tokens_before/after）。
6. `_cleanup_incomplete_messages`：取消时同步 trim `_message_seqs`。

**理由**：
- SessionWriter 必填：杜绝"忘记持久化"的隐式 bug。
- `_message_seqs` 是 summarize 的关键索引：要知道哪些 seq 被摘要了，才能写 `summary_event.summarized_seqs`。
- 索引约定：`self.messages[0]` 是 system prompt（不入 session），所以 `messages[i] ↔ _message_seqs[i-1]`。注释里反复强调，但**这是脆弱点**（见 §3.1）。

### 2.10 修改：`mini_agent/cli.py`（+706 / -100）

**新增顶级函数**：
- `resolve_session_prefix(prefix, store, any_workspace=False) → str`：用户传短 id 前缀，返回完整 session_id；支持 `--any-workspace` 跨项目搜索。
- `maybe_prompt_in_progress(cwd) → str | None | tuple`：启动时检测 24h 内未结束的 session，交互式询问（数字=resume / n=new / f=fork）。
- `run_repl(agent, session_writer, session, workspace_dir, session_start, config)`：从 `run_agent` 抽出的 REPL 主循环，提升可测试性。
- `_cmd_sessions_list`、`_cmd_resume_switch`、`_cmd_fork_switch`：REPL 内 `/sessions` `/resume` `/fork` 的处理器。

**新增 argparse**：
- `--continue` / `--resume <id>` / `--fork <id>` / `--any-workspace` 顶级 flag。
- `session` 子命令 + `list`/`cat`/`tail`/`rm` 子-子命令。
- `migrate-orphans` 子命令（强制重跑 migration）。

**新增 REPL slash 命令**：`/sessions`、`/session-id`、`/resume [id]`、`/fork [id]`。
**删除**：`/log` 命令、`log` 子命令、`show_log_directory`、`read_log_file`、`_open_directory_in_file_manager`、`get_log_directory`、WordCompleter 中的 `/log`。

**信号处理**（替换原来的 KeyboardInterrupt catch）：
```python
sig_state = {"requested": None}
def _sig_handler(signum, _frame):
    if sig_state["requested"] is not None:
        # 第二次信号：恢复默认 disposition，立刻 raise
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        raise KeyboardInterrupt
    sig_state["requested"] = "interrupt" if signum == signal.SIGINT else "sigterm"
```
**理由**：第一次 Ctrl+C → 优雅退出（写 `session_end` 带 `end_reason=interrupt`），第二次 → 立即退出（用户已经不耐烦了）。这是 CLI 应用的标准模式。

**Model 不匹配警告**：resume/fork 时如果源 session 的 model 与当前配置不同，打印 warning。
**Session banner**：每次启动打印短 id + resume/fork 提示，让用户知道怎么回来。

### 2.11 修改：`mini_agent/tools/memory_manager.py`（+12 / -12）

`_resolve_memory_dir` 改用 `mini_agent.paths.memory_dir(str(self.project_root))`。
**删除**：`hashlib` 导入（不再需要）。
**理由**：memory 目录与 session 目录统一在 `<encoded-cwd>/` 下，迁移由 `migrate.py` 负责。

### 2.12 修改：`mini_agent/acp/__init__.py`（+18 / -3）

构造 Agent 时传入 SessionWriter（acp 协议入口也走新的 session 系统）。

### 2.13 删除：`mini_agent/logger.py`（-241）+ `tests/test_logger.py`（-164）

完全移除。旧的"每会话一个 .log 文件 + 30 天 TTL"被 JSONL session 系统完整替代。

### 2.14 测试文件

| 文件 | 行数 | 覆盖 |
|---|---|---|
| `tests/conftest.py` | +20 | `mini_agent_home` fixture（含 HOME 环境变量，子进程安全） |
| `tests/test_session_paths.py` | 40 | encode_cwd 边界（空、根、多斜杠） |
| `tests/test_session_events.py` | 99 | schema 校验 + discriminated type |
| `tests/test_session_locker.py` | 82 | 阻塞/非阻塞/probe/多进程 |
| `tests/test_session_writer.py` | 220 | 生命周期 + 5 个 append + redaction + externalize + fsync |
| `tests/test_session_external_ref.py` | (并入 writer) | > 10 KB 内容落盘 + sha256 |
| `tests/test_session_reader.py` | 144 | raw/compacted + 半行/未知 type/丢失 external_ref |
| `tests/test_session_store.py` | 129 | list/find_latest/find_in_progress/fork/TTL |
| `tests/test_session_migrate.py` | 141 | marker 幂等 + cwd 快路径 + 误报过滤 + 环境变量 |
| `tests/test_session_cli.py` | 90 | list/cat(json)/rm/缺失 session/多 session list |
| `tests/test_session_e2e.py` | 142 | 完整 lifecycle + summary_event + lock + partial line + TTL |
| `tests/test_agent_session_writer.py` | 55 | Agent 必填 session_writer + add_user_message 持久化 + _message_seqs |
| `tests/test_cli_session_flags.py` | 45 | `--help` 列出新 flag + session 子命令 |
| `tests/test_cli_session_init.py` | 103 | --resume 加载 compacted + resolve_session_prefix |
| `tests/test_cli_inprogress_prompt.py` | 50 | 启动提示逻辑（mocked input） |

**完整套件**：294 passed, 3 skipped（`test_agent.py` 的 2 个用例需要真实 API key）。

---

## 3. 当前问题与已知限制

### 3.1 `_message_seqs` 索引脆弱（严重度：中）

`self._message_seqs[i]` 对应 `self.messages[i+1]`（system prompt 在 messages[0] 但不入 seq 列表）。这种 off-by-one 索引在 `_summarize_messages` 和 `_cleanup_incomplete_messages` 里都有显式 `- 1` 操作，注释也写了，但**任何新增的 messages 操作都必须同步维护 _message_seqs，否则索引错位**。

**症状**：summary_event 的 `summarized_seqs` 会写错；compacted 模式跳过错误的 seq。

**临时缓解**：代码里加了 4 处注释强调该不变量。
**根治方案**：见 §4.1。

### 3.2 Resume/Fork 后的 `_message_seqs` 用 `-1` sentinel（严重度：中）

```python
# run_agent 在加载 resumed messages 后：
for msg in loaded_messages:
    agent.messages.append(msg)
    agent._message_seqs.append(-1)   # ← 已经持久化过，seq 未知
```

如果 resumed session 在本次运行内再次触发 summarize，`summarized_seqs` 会包含 `-1`，写出的 summary_event 对后续 reader 无意义（reader 会跳过 -1，但语义混乱）。

**根治方案**：见 §4.2。

### 3.3 软切换（/resume、/fork）后 closure 引用悬空（严重度：低）

`run_repl` 的 `finally` 块在 `run_agent` 里，捕获的 `session_writer` 是原始 writer。软切换后 `agent.session` 已经指向新 writer，但 `run_agent` 的 finally 还会调用**旧** writer 的 `close()`。

**目前不出问题**：旧 writer 在 `_cmd_resume_switch` 里已经被 close 过，再次 close 是 idempotent（`_opened` flag 防重入）。

**代码味道**：依赖 idempotent 的隐式约定。重构方案见 §4.3。

### 3.4 `maybe_prompt_in_progress` 的 fork 分支写死第一个候选（严重度：低）

```python
if choice == "f":
    return ("fork", candidates[0].session_id)   # ← 无法选第 2、3 个
```

用户如果有多个 in-progress session，输入 `f` 只能 fork 最老的那个。

### 3.5 `resolve_session_prefix(any_workspace=True)` 性能差（严重度：低）

```python
for proj in projects_root.iterdir():
    other_store = SessionStore(str(proj))   # ← 每个 project 一个 store
    for m in other_store.list_sessions():   # ← 每个 store 全量 list
        ...
```
projects 目录下项目多时是 O(N×M) 扫描。应该直接 glob `*.jsonl` 用文件名匹配。

### 3.6 Pylance lint 噪音（严重度：很低）

- `cli.py` 的 `_sig_handler(signum, _frame)`：Python signal API 强制要求 frame 参数，已经用 `_` 前缀但 Pylance 仍报警。
- `cli.py:1502` 的 `suffix = "es" if ...`：**这是 pre-existing dead code**（来自 commit `51f93e8`，引入 /init 时遗留），不属于本次范围。

### 3.7 测试覆盖盲区（严重度：中）

| 场景 | 当前测试 |
|---|---|
| `/resume` 软切换 | ❌ 无（只测了启动时的 --resume） |
| `/fork` 软切换 | ❌ 无 |
| `session tail -f` follow 模式 | ❌ 无（只覆盖了非 follow 路径） |
| `session cat --expand-external` 真正展开 | ❌ 无（代码本身也是 placeholder） |
| migrate_all 的 Spotlight/locate fallback | ❌ 无（只测了 marker + cwd 快路径） |
| 模型不匹配警告 | ❌ 无 |
| 信号第二次立即退出的路径 | ❌ 无 |

### 3.8 `session cat --expand-external` 实际是 placeholder（严重度：低）

```python
if expand:
    print(f"  [external_ref size={...} sha256={...}...]")  # ← 跟 else 分支一样
```
没真正读 external_ref 文件展开内容。当时为了赶进度先写了占位。

### 3.9 跨平台限制（严重度：低，但 Windows 用户会踩坑）

- `locker.py` 用 `fcntl.flock`：仅 POSIX。
- `migrate.py` 的 Spotlight 快路径：仅 macOS。
- 信号处理：Windows 的 SIGTERM 语义不同。

设计文档里明确非目标是 Windows，但代码里没有任何早期失败提示。Windows 用户运行会得到模糊的 `ImportError` 或静默退化。

### 3.10 `mini_agent.cli main()` 测试依赖真实 config（严重度：中）

`test_cli_resume_loads_compacted_messages` 通过 `main()` 端到端走流程，会调用 `Config.from_yaml()` 加载真实的 `mini_agent/config/config.yaml`。CI 环境如果没有 API key 配置，这个测试会失败或走错路径。

**临时缓解**：本地开发环境都有 config.yaml。
**根治方案**：见 §4.6。

---

## 4. 后续改进建议

按优先级（影响 × 紧迫性）排序。

### 4.1 ★★★ 把 `_message_seqs` 改成 message 本身的属性

**问题**：§3.1 的索引脆弱。
**方案**：在 `Message` schema 加一个可选的 `seq: int | None` 字段（不参与 LLM 调用，纯元数据）。

```python
class Message(BaseModel):
    role: str
    content: ...
    seq: int | None = None   # ← 新增
```

然后：
- `agent.add_user_message` 时把返回的 seq 直接挂到 message 上。
- summarize 时直接遍历 `messages`，不再需要平行列表。
- 删除 `_message_seqs` 字段。

**收益**：消除 off-by-one，消除平行维护负担。
**代价**：Message schema 改动会影响 LLM 序列化（要确保 `seq` 不被发给 LLM —— 在序列化时显式 exclude）。

### 4.2 ★★★ Resume 时真正读取历史 seq

**问题**：§3.2 的 `-1` sentinel。
**方案**：`SessionReader.load_messages()` 返回值改成 `list[tuple[Message, int]]`（或单独提供 `load_messages_with_seq()`）。

```python
def load_messages_with_seq(self, sid, mode="compacted") -> list[tuple[Message, int]]:
    # 遍历 events 时，message 类型的 event.seq 直接配对返回
```

`run_agent` resume 分支：
```python
for msg, seq in reader.load_messages_with_seq(resume_id, mode="compacted"):
    agent.messages.append(msg)
    # 不再 append_user_message（避免重复写 JSONL）
```

Agent 提供 `add_loaded_message(msg, seq)` 方法区分新写入 vs 加载。

### 4.3 ★★ 软切换重构：SessionContext 模式

**问题**：§3.3 closure 悬空。
**方案**：引入可变容器：

```python
@dataclass
class SessionContext:
    agent: Agent
    writer: SessionWriter
    session: PromptSession  # prompt_toolkit
    workspace_dir: Path
    config: Config
    session_start: datetime

async def run_repl(ctx: SessionContext):
    while True:
        ...
        # 软切换时：
        ctx.writer = new_writer
        ctx.agent.session = new_writer
```

`run_agent` 的 finally 引用 `ctx.writer`，永远拿到当前值。

### 4.4 ★★ 补 `/resume` `/fork` 软切换的集成测试

**问题**：§3.7 的盲区。
**方案**：写一个 `test_cli_soft_switch.py`，模拟：
1. 创建 session A，写入消息。
2. 启动新 Agent，软切换到 A，验证 `agent.messages` 包含 A 的消息。
3. 软切换 fork A，验证 fork 后是新 session_id 且 messages 一致。

可以用 `mock.patch` 替换 PromptSession 的 `prompt_async`，让它依次返回 `/resume A`、`/exit`。

### 4.5 ★★ `session cat --expand-external` 真正展开

**问题**：§3.8 placeholder。
**方案**：reader 加一个 `read_external_ref(event)` 方法，返回文件内容（带 sha256 校验）。`_cmd_cat` 的 expand 分支调用它打印内容。

### 4.6 ★★ 把 `main()` 测试与 config 解耦

**问题**：§3.10。
**方案**：把 session 分支逻辑（new/resume/fork）从 `run_agent` 抽出成纯函数：

```python
def resolve_session_branch(args, cwd, workspace_dir, model) -> SessionBranch:
    """返回 (resume_id, fork_id, new_id)，不依赖 config / LLM。"""
```

测试这个纯函数即可，不需要走完整 `main()`。

### 4.7 ★ `maybe_prompt_in_progress` fork 分支让用户选

```python
if choice == "f":
    # 二级菜单
    for i, m in enumerate(candidates, 1):
        print(f"   [{i}] fork {m.session_id[:8]}")
    sub = input("   fork which? > ").strip()
    if sub.isdigit() and 0 <= int(sub)-1 < len(candidates):
        return ("fork", candidates[int(sub)-1].session_id)
```

### 4.8 ★ `resolve_session_prefix(any_workspace=True)` 直接 glob

```python
if any_workspace:
    projects_root = Path.home() / ".mini-agent" / "projects"
    for jsonl in projects_root.glob("*/sessions/*.jsonl"):
        if jsonl.stem.startswith(prefix):
            matches.append(jsonl.stem)
```
O(匹配数) 而非 O(项目数 × 每项目 session 数)。

### 4.9 ★ Schema 版本升级路径

`SessionEvent.schema_version` 字段已经存在但没用。建议：
- 加 `upgrade_event(event) -> event` 函数，按版本号链式升级。
- reader 在 `iter_events` 里自动调用 upgrade。
- 为未来 schema 演进留口子。

### 4.10 ★ Session 压缩归档

JSONL 长会话（> 1 MB）读取慢。建议：
- 30 天以上未访问的 session 自动 gzip（保留 `*.jsonl.gz`，reader 透明读取）。
- `session list` 显示压缩标志。

### 4.11 ★ Windows 兼容性 or 早期失败

要么用 `msvcrt.locking` 实现一个 Windows 版 FileLock，要么在 `cli.py main()` 启动时检测 `os.name == 'nt'` 并打印 "Windows not supported, use WSL"。

### 4.12 ★★ 跨 Agent 实例的并发安全

当前 `SessionWriter` 用 flock 防止同一 session 被两个进程同时写。但**没有防止同一进程内两个 Agent 实例同时引用同一个 SessionWriter**（理论上不会发生，但 soft-switch 时如果忘记切换引用，可能踩坑）。

建议：`SessionWriter.append_*` 加一个 `_owner_pid` 字段，写入时断言 `os.getpid() == _owner_pid`。

### 4.13 ★ session 元数据扩展

当前 `SessionMeta` 只有 model、forked_from、preview 等。考虑加：
- `title`（用户可编辑，类似 Claude Code 的"First user message"自动标题）。
- `tags: list[str]`。
- `pinned: bool`（不受 TTL 影响）。

需要 `mini-agent session rename`、`mini-agent session tag` 子命令配套。

### 4.14 ★ `summary_event` 内容归档

当前 summary_event 只记录被摘要的 seq 范围和 tokens，不存摘要正文。如果未来想做"对话考古"（看每次摘要的演进），需要把摘要正文也写入 event（或者作为外部 ref）。

---

## 5. 设计决策回顾（事后审视）

下表对照最初设计 spec 和最终实现，记录偏离的地方和原因。

| 决策 | 设计 spec | 实际实现 | 偏离原因 |
|---|---|---|---|
| Session ID 格式 | `str(uuid.uuid4())` | `uuid.uuid4().hex[:12]` | 短 ID 更适合在 banner / `--resume` 中显示；冲突概率在单项目内可忽略 |
| `run_repl(agent, writer)` 签名 | 只两个参数 | `(agent, session_writer, session, workspace_dir, session_start, config)` | REPL body 实际依赖更多状态；为了测试可 mock，全部显式传入 |
| `_message_seqs` 设计 | "平行 list 追踪 seq" | 同设计 | 设计时低估了索引维护成本，见 §3.1 |
| Resume 后 `_message_seqs` | 设计未明确 | 用 `-1` sentinel | 设计时未考虑 resume 后再次 summarize 的场景 |
| `--any-workspace` 实现 | 设计只说"扫描所有 projects" | 嵌套 SessionStore（次优） | 赶进度，应该用 glob（§4.8） |
| Windows 支持 | 设计 spec 明确非目标 | 同设计 | 维持 |
| `migrate-orphans` 子命令 | 设计说"interactive resolution" | 实际只是 `migrate_all(force=True)` 的别名 | 完整 interactive 流程未实现，先给了"强制重跑"的逃生口 |

---

## 6. 给后续维护者的建议

1. **改 Agent 的 messages 操作时**：同步检查 `_message_seqs` 维护。短期靠注释；长期按 §4.1 重构。
2. **加新 SessionEvent type**：先在 `events.py` 加字段，再在 reader 的 `iter_events` 加 type 分支，最后在 writer 加 `append_*` 方法。三处必须同步。
3. **改 schema**：必须 bump `schema_version` 并在 reader 加 upgrade 路径（见 §4.9）。
4. **跨平台扩展**：所有文件锁、信号、`subprocess.run(["open", ...])` 调用都假设 POSIX。Windows 支持必须按平台分支，不能简单替换 API。
5. **测试**：新增功能必须有 `tests/test_session_*.py` 或 `tests/test_cli_*.py`；e2e 测试放 `test_session_e2e.py`。CLI 子命令测试用 `subprocess.run` + `cwd=tmp_path` + `mini_agent_home` fixture（HOME 环境变量会被 fixture 设置，子进程能继承）。
6. **磁盘格式兼容**：JSONL 一旦发布就不能改字段语义（只能加新字段）。需要破坏性改动时，bump schema_version + 提供 upgrade。

---

## 7. 参考资料

- 原始设计 spec：`docs/superpowers/specs/2026-06-17-session-management-design.md`
- 19 任务实施计划：`docs/superpowers/plans/2026-06-18-session-management.md`
- Claude Code 路径布局（参考实现）：`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`
- POSIX `fcntl.flock` 手册：`man 2 flock`（macOS）/ `man 2 fcntl`（Linux）
- Pydantic discriminated union：https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions

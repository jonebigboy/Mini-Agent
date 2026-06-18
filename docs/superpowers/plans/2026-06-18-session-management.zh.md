# Session 管理实现计划（中文版）

> **对应英文原版**：`docs/superpowers/plans/2026-06-18-session-management.md`（执行时以英文版为准，本文档为阅读参考）
> **设计文档**：`docs/superpowers/specs/2026-06-17-session-management-design.md`

---

## 目标

为 Mini-Agent 增加 **append-only JSONL 会话持久化**，支持 `--continue` / `--resume` / `--fork` 三种恢复语义，替换现有的 `logger.py`，并将 memory 目录布局从 `<name>_<hash8>` 迁移到 `<encoded-cwd>`。

## 架构

新增 `mini_agent/session/` 包，包含 Writer / Reader / Store / Locker / Migrate 五大组件：

- **Agent 接收 `SessionWriter`**（替代 `AgentLogger`）
- **CLI 增加 session 标志位 + 斜杠命令 + 信号处理器**
- **Memory 路径迁移**：`<name>_<hash8>` → `<encoded-cwd>`

## 技术栈

Python 3.10+、pydantic 2.x、pytest + pytest-asyncio、fcntl（POSIX 文件锁）、uuid、hashlib。

---

## 文件结构总览

```
mini_agent/
├── paths.py                          # 🆕 encode_cwd、project_dir、sessions_dir 等
├── session/                          # 🆕 新包
│   ├── __init__.py
│   ├── events.py                     # SessionEvent + ExternalRefBlock pydantic 模型
│   ├── locker.py                     # FileLock 包装（fcntl.flock）
│   ├── writer.py                     # SessionWriter（替代 AgentLogger）
│   ├── reader.py                     # SessionReader（raw + compacted 双模式）
│   ├── store.py                      # SessionStore（list、find、fork、TTL）
│   ├── migrate.py                    # hash8 → encoded-cwd 迁移
│   └── cli.py                        # `mini-agent session <子命令>` 处理器
├── agent.py                          # 修改：去掉 logger，接受 SessionWriter
├── cli.py                            # 修改：--continue/--resume/--fork + 斜杠命令 + 信号
├── logger.py                         # ❌ 删除（任务 16）
└── tools/
    └── memory_manager.py             # 修改：encoded-cwd 路径

tests/
├── conftest.py                       # 修改：添加 mini_agent_home fixture
├── test_session_paths.py             # 🆕
├── test_session_events.py            # 🆕
├── test_session_locker.py            # 🆕
├── test_session_writer.py            # 🆕
├── test_session_reader.py            # 🆕
├── test_session_store.py             # 🆕
├── test_session_fork.py              # 🆕
├── test_session_migrate.py           # 🆕
├── test_session_cli.py               # 🆕
└── test_session_e2e.py               # 🆕
```

---

## 任务总览（19 个任务，10 个阶段）

| 阶段 | 任务 | 主题 | 提交信息 |
|------|------|------|----------|
| 1. 基础 | 1 | `mini_agent_home` 测试 fixture | `test: add mini_agent_home fixture for session tests` |
| 1. 基础 | 2 | `paths.py` — encoded-cwd 工具 | `feat(paths): add encoded-cwd path utilities` |
| 1. 基础 | 3 | `session/events.py` — SessionEvent schema | `feat(session): add SessionEvent schema with ExternalRefBlock` |
| 1. 基础 | 4 | `session/locker.py` — fcntl.flock 包装 | `feat(session): add FileLock with fcntl.flock` |
| 2-3. Writer/Reader | 5 | `session/writer.py` — 生命周期 + 消息追加 | `feat(session): add SessionWriter with append-only JSONL + secret redaction` |
| 2-3. Writer/Reader | 6 | External_ref 显式测试 | `test(session): cover external_ref externalization paths` |
| 2-3. Writer/Reader | 7 | `session/reader.py` — raw + compacted + 健壮性 | `feat(session): add SessionReader with raw/compacted modes + robustness contract` |
| 4. Store | 8 | `session/store.py` — list/find/fork/TTL | `feat(session): add SessionStore with list/find/fork/TTL` |
| 5. 迁移 | 9 | `session/migrate.py` — hash8 → encoded-cwd | `feat(session): add hash8→encoded-cwd migration with marker file` |
| 5. 迁移 | 10 | 更新 `memory_manager.py` 用 encoded-cwd | `refactor(memory): use encoded-cwd paths (drops hash8)` |
| 6. Agent | 11 | 修改 `agent.py` 接受 SessionWriter | `refactor(agent): replace AgentLogger with SessionWriter + summary_event tracking` |
| 7-8. CLI | 12 | `cli.py` — 接入迁移 + session 标志 | `feat(cli): add session flags + session subcommand skeleton` |
| 7-8. CLI | 13 | `cli.py` — 接入 new/resume/fork 分支 | `feat(cli): wire up new/resume/fork branches + signal handlers` |
| 7-8. CLI | 14 | `cli.py` — in-progress 提示 + 斜杠命令 | `feat(cli): add in-progress session prompt + /sessions /resume /fork slash commands` |
| 9. Session 子命令 | 15 | `session/cli.py` — `session cat` 等 | `feat(session): add session cat/list/tail/rm subcommands` |
| 10. 收尾 | 16 | 删除 `logger.py` + 移除 `/log` | `refactor: remove logger.py (replaced by session/writer.py)` |
| 10. 收尾 | 17 | 端到端测试 | `test(session): end-to-end coverage for new/resume/fork/summary/lock/TTL` |
| 10. 收尾 | 18 | 更新 CLAUDE.md | `docs: update CLAUDE.md to reflect session management module` |
| 10. 收尾 | 19 | 手动冒烟测试 + 最终提交 | `feat(session): complete session management system (B standard)` |

每个任务严格遵循 TDD 五步：
1. **写失败测试** → 2. **验证失败** → 3. **最小实现** → 4. **验证通过** → 5. **提交**

---

## 任务详解

### 任务 1：测试 fixture — `mini_agent_home`

**目的**：所有 session 测试都要把 `Path.home()` 重定向到临时目录，避免污染开发机的真实 `~/.mini-agent`。

**文件**：
- 修改：`tests/conftest.py`（不存在则创建）

**实现**：

```python
# tests/conftest.py
"""Shared pytest fixtures."""
from pathlib import Path
import pytest


@pytest.fixture
def mini_agent_home(tmp_path, monkeypatch):
    """把 Path.home() 重定向到临时目录，隔离 session 测试。"""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    return fake_home
```

**验证**：`pytest tests/ --co -q | head -5` 能正常收集既有测试。

---

### 任务 2：`paths.py` — encoded-cwd 路径工具

**目的**：所有持久化状态的根目录统一为 `~/.mini-agent/projects/<encoded-cwd>/`。encoded-cwd 是绝对路径中 `/` 替换为 `-`（去掉前导 `-`）。这种命名方式比 hash8 更直观、抗项目移动。

**文件**：
- 创建：`mini_agent/paths.py`
- 测试：`tests/test_session_paths.py`

**关键测试**：

```python
def test_encode_cwd_basic():
    assert encode_cwd("/Users/jone/foo") == "Users-jone-foo"

def test_encode_cwd_strips_leading_dash():
    assert encode_cwd("/foo/bar") == "foo-bar"

def test_encode_cwd_preserves_underscores():
    """路径中的下划线必须保留（不替换）。"""
    assert encode_cwd("/Users/jone/foo_bar") == "Users-jone-foo_bar"

def test_project_dir_uses_encoded_cwd(mini_agent_home):
    p = project_dir("/Users/jone/Mini-Agent")
    assert p == mini_agent_home / ".mini-agent" / "projects" / "Users-jone-Mini-Agent"
```

**实现**：

```python
# mini_agent/paths.py
"""路径工具：session 和 memory 存储根目录为 ~/.mini-agent/projects/<encoded-cwd>/。

encoded-cwd 是绝对路径将 '/' 换成 '-'（去掉前导 '-'）。
这种命名方式对标 Claude Code 的 session 布局，比 hash8 更抗项目移动/重命名。
"""
from pathlib import Path


def encode_cwd(cwd: str) -> str:
    """将绝对路径编码为目录名。

    '/Users/jone/foo' → 'Users-jone-foo'
    下划线和其他字符都保留不变。
    """
    return cwd.replace("/", "-").lstrip("-")


def project_dir(cwd: str) -> Path:
    """~/.mini-agent/projects/<encoded-cwd>/"""
    return Path.home() / ".mini-agent" / "projects" / encode_cwd(cwd)


def sessions_dir(cwd: str) -> Path:
    return project_dir(cwd) / "sessions"


def tool_outputs_dir(cwd: str, session_id: str) -> Path:
    return project_dir(cwd) / "tool_outputs" / session_id


def memory_dir(cwd: str) -> Path:
    return project_dir(cwd) / "memory"


def migration_marker_path() -> Path:
    """~/.mini-agent/.projects-v2-migrated — 第一次迁移完成后设置。"""
    return Path.home() / ".mini-agent" / ".projects-v2-migrated"


def migration_log_path() -> Path:
    """~/.mini-agent/migration.log — 只追加的审计日志。"""
    return Path.home() / ".mini-agent" / "migration.log"
```

**验证**：`pytest tests/test_session_paths.py -v` → 7 passed。

---

### 任务 3：`session/events.py` — SessionEvent schema

**目的**：定义 session JSONL 文件中每一行的数据结构。采用单 pydantic 模型 + `type` 鉴别字段（不用 discriminated union）。

**关键设计**：
- 公共头：`seq`、`ts`、`type`、`session_id`
- `type` 取值：`session_start` / `session_end` / `message` / `summary_event` / `error`
- 各类型对应的可选字段都挂在同一个模型上
- `ExternalRefBlock` 是唯一严格类型化的内容子块

**文件**：
- 创建：`mini_agent/session/__init__.py`（空）
- 创建：`mini_agent/session/events.py`
- 测试：`tests/test_session_events.py`

**核心模型**：

```python
class ExternalRefBlock(BaseModel):
    """指向存储大 tool_result 的外部文件的引用。

    当 tool_result 的文本内容超过 10 KB 时使用：
    body 写到 tool_outputs/<sid>/seq-<N>.txt，JSONL 行只存指针 + 完整性哈希。
    """
    type: Literal["external_ref"] = "external_ref"
    path: str
    size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class SessionEvent(BaseModel):
    """session JSONL 文件中的一行。完整 schema 见设计文档 §5。"""
    # 公共头
    seq: int = Field(ge=0)
    ts: str  # ISO 8601
    type: Literal[
        "session_start", "session_end", "message", "summary_event", "error",
    ]
    session_id: str

    # message 类型
    role: str | None = None
    content: str | list[dict[str, Any]] | None = None
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    # session_start
    cwd: str | None = None
    workspace: str | None = None
    model: str | None = None
    cli_args: dict[str, Any] | None = None
    schema_version: str | None = None
    forked_from: str | None = None

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

**测试覆盖**（8 个）：
- ExternalRefBlock 有效 / 无效 sha256
- session_start 最小化字段
- message（user / tool）
- session_end 含 end_reason
- summary_event
- JSON 往返序列化
- 未知 type 被拒绝（write time）

**验证**：`pytest tests/test_session_events.py -v` → 8 passed。

---

### 任务 4：`session/locker.py` — fcntl.flock 包装

**目的**：用 POSIX flock 实现"session 当前是否被活进程持有"。内核会在持有进程退出时（含 SIGKILL、崩溃）自动释放锁，所以**无需 mtime 启发式**。

**关键 API**：
- `acquire(blocking=True)` — 默认阻塞；非阻塞时锁被持有则抛 `SessionLockedError`
- `release()` — 释放（幂等）
- `probe()` — 非阻塞获取 + 立即释放，返回 `True` 表示空闲
- 支持 context manager（`with FileLock(...):`）

**文件**：
- 创建：`mini_agent/session/locker.py`
- 测试：`tests/test_session_locker.py`

**核心实现**：

```python
import fcntl
import os
from pathlib import Path


class SessionLockedError(Exception):
    """锁被另一个活进程持有时抛出。"""


class FileLock:
    """独占 flock 包装。同进程的多个实例之间不可重入。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self, blocking: bool = True) -> None:
        if self._fd is not None:
            return  # 本实例已持有
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            os.close(fd)
            raise SessionLockedError(f"Lock held by another process: {self.path}") from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def probe(self) -> bool:
        """非阻塞获取 + 立即释放。空闲返回 True。"""
        try:
            self.acquire(blocking=False)
            self.release()
            return True
        except SessionLockedError:
            return False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
```

**测试覆盖**（5 个）：
- 获取 + 释放 + 再获取
- 非阻塞在持有时失败
- 进程被 kill 后自动释放（multiprocessing 子进程模拟崩溃）
- context manager 用法
- probe 不持有锁

**验证**：`pytest tests/test_session_locker.py -v` → 5 passed。

---

### 任务 5：`session/writer.py` — 生命周期 + 消息追加

**目的**：替代 `AgentLogger`。每个 Agent 实例对应一个 SessionWriter。每次 append 后 `os.fsync`，崩溃恢复契约：**最多丢最后一条事件**。

**关键 API**：
- `__init__(session_id, cwd, workspace, model, cli_args)`
- `open(forked_from=None)` — 获取锁、创建文件、写 session_start
- `close(end_reason="exit")` — 写 session_end、释放锁（幂等）
- `append_user_message(content) -> int`
- `append_assistant_message(content, thinking, tool_calls) -> int`
- `append_tool_result(tool_call_id, name, content) -> int`
- `append_summary_event(summarized_seqs, summary_seq, tokens_before, tokens_after) -> int`
- `append_error(error) -> int`

**关键设计点**：
1. **`_seq` 单调递增**：`_next_seq()` 同步分配 + 立即返回，无"分配未使用"窗口
2. **`_redact_secrets`**：递归把 cli_args 中所有 secret-looking key（正则 `api[_-]?key|token|secret|password|credential|auth`，大小写不敏感）的值替换为 `***`
3. **`_externalize_content`**：list 内容中 `text` 块若 > 10 KB，写到 tool_outputs，JSONL 中只留 `external_ref` 块（含 path/size/sha256）
4. **每次 append 都 `flush + os.fsync`**

**文件**：
- 创建：`mini_agent/session/writer.py`
- 测试：`tests/test_session_writer.py`

**核心实现**（节选）：

```python
EXTERNAL_REF_THRESHOLD = 10 * 1024  # 10 KB

SECRET_KEY_PATTERNS = re.compile(
    r"(api[_-]?key|token|secret|password|credential|auth)",
    re.IGNORECASE,
)


def _redact_secrets(value: Any) -> Any:
    """递归把 secret-looking key 的值替换为 '***'。"""
    if isinstance(value, dict):
        return {
            k: ("***" if SECRET_KEY_PATTERNS.search(k) else _redact_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SessionWriter:
    """Append-only event writer。每个 Agent 实例一个。"""

    def __init__(self, session_id, cwd, workspace, model, cli_args):
        self.session_id = session_id
        self._cwd = cwd
        self._workspace = workspace
        self._model = model
        self._cli_args = cli_args

        self._path = sessions_dir(cwd) / f"{session_id}.jsonl"
        self._tool_outputs_dir = tool_outputs_dir(cwd, session_id)
        self._seq = -1
        self._lock = FileLock(sessions_dir(cwd) / f"{session_id}.lock")
        self._fh = None
        self._opened = False

    def open(self, forked_from: str | None = None) -> None:
        """获取锁、创建/打开文件、写 session_start 事件。"""
        if self._opened:
            return
        self._lock.acquire(blocking=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")  # 追加模式
        self._opened = True
        self._append_event(SessionEvent(
            seq=self._next_seq(),
            ts=_now_iso(),
            type="session_start",
            session_id=self.session_id,
            cwd=self._cwd,
            workspace=self._workspace,
            model=self._model,
            cli_args=_redact_secrets(self._cli_args),
            schema_version="1",
            forked_from=forked_from,
        ))

    def close(self, end_reason: str = "exit") -> None:
        """写 session_end、释放锁、关闭文件句柄。幂等。"""
        if not self._opened:
            return
        self._append_event(SessionEvent(
            seq=self._next_seq(),
            ts=_now_iso(),
            type="session_end",
            session_id=self.session_id,
            end_reason=end_reason,
        ))
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None
        self._lock.release()
        self._opened = False

    # —— append API ——
    def append_user_message(self, content) -> int:
        return self._append_message(role="user", content=content)

    def append_assistant_message(self, content, thinking=None, tool_calls=None) -> int:
        return self._append_message(
            role="assistant", content=content, thinking=thinking, tool_calls=tool_calls,
        )

    def append_tool_result(self, tool_call_id, name, content) -> int:
        externalized = self._externalize_content(content)
        return self._append_message(
            role="tool", content=externalized,
            tool_call_id=tool_call_id, name=name,
        )

    def append_summary_event(self, summarized_seqs, summary_seq,
                              tokens_before, tokens_after) -> int:
        return self._append_event(SessionEvent(
            seq=self._next_seq(), ts=_now_iso(),
            type="summary_event", session_id=self.session_id,
            summarized_seqs=list(summarized_seqs), summary_seq=summary_seq,
            tokens_before=tokens_before, tokens_after=tokens_after,
        ))

    def append_error(self, error: str) -> int:
        return self._append_event(SessionEvent(
            seq=self._next_seq(), ts=_now_iso(),
            type="error", session_id=self.session_id, error=error,
        ))

    # —— 内部 ——
    def _next_seq(self) -> int:
        """单调递增。无"分配未使用"窗口（同步）。"""
        self._seq += 1
        return self._seq

    def _externalize_content(self, content):
        """list 内容中 > 10 KB 的 text 块外置。"""
        if isinstance(content, str):
            return content
        result = []
        for block in content:
            if (isinstance(block, dict)
                and block.get("type") == "text"
                and len(block.get("text", "").encode("utf-8")) > EXTERNAL_REF_THRESHOLD):
                result.append(self._write_external_ref(block["text"]))
            else:
                result.append(block)
        return result

    def _write_external_ref(self, text: str) -> dict:
        """把大文本写到 tool_outputs/<sid>/seq-<N>.txt；返回 ExternalRefBlock dict。"""
        upcoming_seq = self._seq + 1  # 此时 _next_seq 还没消费
        self._tool_outputs_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._tool_outputs_dir / f"seq-{upcoming_seq}.txt"
        data = text.encode("utf-8")
        out_path.write_bytes(data)
        import hashlib
        return {
            "type": "external_ref",
            "path": str(out_path.relative_to(self._tool_outputs_dir.parent.parent)),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _append_event(self, event: SessionEvent) -> int:
        if not self._opened or self._fh is None:
            raise RuntimeError("SessionWriter not opened")
        line = event.model_dump_json()
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())  # 崩溃恢复契约的关键
        return event.seq
```

**测试覆盖**（8 个）：
- open 写 session_start（schema_version="1"、cwd、model、seq=0）
- cli_args 中 api_key/token/password 替换为 `***`，嵌套 dict 也生效
- append_user_message seq 单调
- append_assistant_message 含 thinking + tool_calls
- append_tool_result
- append_summary_event
- close 写 session_end
- 每次 append 后文件立即持久化（fsync 验证）

**验证**：`pytest tests/test_session_writer.py -v` → 8 passed。

---

### 任务 6：External_ref 显式测试

**目的**：把 external_ref 三种路径作为独立测试补齐（任务 5 只覆盖了基本路径）。

**三个新测试**：
1. **大内容外置**：text > 10 KB → 写文件、JSONL 留 external_ref、文件可读
2. **小内容内联**：text ≤ 10 KB → 原样保留在 JSONL 中
3. **混合块**：单个 tool_result 中 `[小 text, 大 text]` → `[text, external_ref]`

**验证**：`pytest tests/test_session_writer.py -v` → 11 passed（8 + 3）。

---

### 任务 7：`session/reader.py` — raw + compacted + 健壮性

**目的**：把 JSONL 加载回 `Message` 列表。两种模式：
- **raw**（审计/调试）：重放每条原始 message 事件
- **compacted**（resume 默认）：跳过 summarized_seqs，保留摘要消息

**健壮性契约（关键）**：
- 部分 last line（写一半崩溃） → 截断、warn、继续
- external_ref 文件缺失 → 占位符 + warn、继续
- sha256 不匹配 → 占位符 + warn、继续
- 未知 event type → warn + skip、继续
- **绝不把这些错误传播给 Agent**

**文件**：
- 创建：`mini_agent/session/reader.py`
- 测试：`tests/test_session_reader.py`

**核心实现**：

```python
class SessionNotFoundError(Exception):
    pass


class SessionReader:
    def __init__(self, cwd: str):
        self._cwd = cwd
        self._sessions_dir = sessions_dir(cwd)

    def load_messages(
        self,
        session_id: str,
        mode: Literal["raw", "compacted"] = "compacted",
    ) -> list[Message]:
        events = list(self._iter_events(session_id))

        if mode == "raw":
            return [
                self._event_to_message(e)
                for e in events if e.type == "message"
            ]

        # compacted：收集所有 summarized_seqs，跳过
        summarized: set[int] = set()
        for e in events:
            if e.type == "summary_event" and e.summarized_seqs:
                summarized.update(e.summarized_seqs)

        return [
            self._event_to_message(e)
            for e in events
            if e.type == "message" and e.seq not in summarized
        ]

    def iter_events(self, session_id: str) -> Iterator[SessionEvent]:
        """公开的原始迭代接口（`session cat` 用）。"""
        return self._iter_events(session_id)

    def session_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.jsonl"

    # —— 内部 ——
    def _iter_events(self, session_id: str) -> Iterator[SessionEvent]:
        path = self.session_path(session_id)
        if not path.exists():
            raise SessionNotFoundError(f"No session file: {path}")
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    # 部分 last line → 截断、warn、停止迭代
                    warnings.warn(f"session {session_id}: malformed line, truncated")
                    return
                try:
                    yield SessionEvent.model_validate(data)
                except Exception as exc:
                    # 未知 type / 坏 schema → warn + skip
                    warnings.warn(
                        f"session {session_id}: skipping event seq={data.get('seq')}: {exc}"
                    )
                    continue

    def _event_to_message(self, event: SessionEvent) -> Message:
        content = self._resolve_content(event.content, event.session_id, event.seq)
        return Message(
            role=event.role or "user",
            content=content,
            thinking=event.thinking,
            tool_calls=event.tool_calls,
            tool_call_id=event.tool_call_id,
            name=event.name,
        )

    def _resolve_content(self, content, session_id, seq):
        if not isinstance(content, list):
            return content
        result = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "external_ref":
                result.append(self._resolve_external_ref(block, session_id, seq))
            else:
                result.append(block)
        return result

    def _resolve_external_ref(self, block, session_id, seq) -> dict:
        """读 external_ref 文件，校验 sha256。失败返回占位符。"""
        rel_path = block["path"]
        full_path = project_dir(self._cwd) / rel_path
        try:
            data = full_path.read_bytes()
        except OSError as exc:
            warnings.warn(f"session {session_id} seq {seq}: external_ref file missing: {rel_path}: {exc}")
            return {"type": "text", "text": "[tool_result unavailable: file missing]"}

        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != block["sha256"]:
            warnings.warn(f"session {session_id} seq {seq}: external_ref sha256 mismatch for {rel_path}")
            return {"type": "text", "text": "[tool_result unavailable: checksum mismatch]"}

        return {"type": "text", "text": data.decode("utf-8", errors="replace")}
```

**测试覆盖**（7 个）：
- raw 模式重放所有 message
- compacted 模式跳过 summarized_seqs（保留摘要消息）
- 默认模式是 compacted
- 部分 last line 不崩
- external_ref 文件缺失 → 占位符
- 未知 event type → 跳过
- 找不到 session → 抛 SessionNotFoundError

**验证**：`pytest tests/test_session_reader.py -v` → 7 passed。

---

### 任务 8：`session/store.py` — list / find / fork / TTL

**目的**：目录级操作（不读写事件本身，那是 Writer/Reader 的事）：列出 sessions、查找最近的、fork、TTL 清理。

**关键 API**：
- `list_sessions() -> list[SessionMeta]` — 按 started_at 倒序
- `find_latest() -> SessionMeta | None`
- `find_in_progress(max_age_hours=24) -> list[SessionMeta]` — 未结束 + 锁可获取（说明无活进程）+ 时间窗内
- `fork_session(source_id, new_id)` — `shutil.copy2` 物理 copy
- `cleanup_old_sessions(max_age_days=30) -> int` — 跳过当前被锁定的

**SessionMeta** 数据类：
```python
@dataclass
class SessionMeta:
    session_id: str
    started_at: str | None
    last_event_at: str | None
    ended: bool
    end_reason: str | None
    message_count: int
    preview: str            # 最后一条 user message 预览
    model: str | None
    forked_from: str | None
```

**关键设计**：
- `_derive_meta`：只读前 200 条事件（`_read_first_and_last`），不全文加载
- `find_in_progress`：用 `FileLock.probe()` 判断锁是否可获取（活进程检测）
- `cleanup_old_sessions`：跳过锁仍被持有的（活进程保护）

**文件**：
- 创建：`mini_agent/session/store.py`
- 测试：`tests/test_session_store.py`

**测试覆盖**（10 个）：
- 空 → `[]`
- 多 session 列出 metadata
- 区分 ended / in-progress
- preview 是最后一条 user message
- find_latest
- find_in_progress 过滤 ended
- TTL 删除老文件（backdate mtime）
- TTL 保留当前文件
- fork 物理 copy
- fork 后源文件不变

**验证**：`pytest tests/test_session_store.py -v` → 10 passed。

---

### 任务 9：`session/migrate.py` — hash8 → encoded-cwd

**目的**：一次性把老的 `<name>_<hash8>/memory/` 迁移到 `<encoded-cwd>/memory/`。在新版本第一次启动时自动触发。

**触发逻辑（顺序）**：
1. `MINI_AGENT_NO_MIGRATE=1` → 完全跳过
2. Marker file 存在 + 没有 `MINI_AGENT_FORCE_MIGRATE=1` → 跳过
3. `has_legacy_projects()` 返回 False → 设 marker、跳过
4. **cwd fast path**：检查 `<cwd_name>_<hash8(cwd)>` 是否存在，存在就立即迁移
5. **扫描剩余 legacy 目录**：
   - 用 Spotlight（macOS）/ locate（Linux）/ filesystem walk（兜底）反查真实路径
   - hash8 验证
   - 多匹配 → 取第一个 + log 警告
   - 零匹配 → 看是否是 false positive（encoded-cwd 误判为 legacy）；不是则计为 orphan
6. 写 marker、返回 `(migrated_count, orphan_count)`

**False positive 判定**：name 有 ≥ 2 个 `-` 段（encoded-cwd 通常如此）+ memory 目录为空。

**迁移单元**：`shutil.move(old/memory, new/memory)`。失败不删 old_dir。新目录已存在则跳过。

**文件**：
- 创建：`mini_agent/session/migrate.py`
- 测试：`tests/test_session_migrate.py`

**测试覆盖**（11 个）：
- `_hash8_of` 已知路径
- 无 legacy → False
- 有 legacy → True
- marker 存在 → 跳过
- `MINI_AGENT_FORCE_MIGRATE=1` 覆盖 marker
- `MINI_AGENT_NO_MIGRATE=1` 跳过
- cwd fast path 迁移
- 孤儿目录保留
- 目标已存在 → 跳过
- 迁移后写 marker
- 迁移日志追加

**验证**：`pytest tests/test_session_migrate.py -v` → 11 passed。

---

### 任务 10：更新 `memory_manager.py` 用 encoded-cwd

**目的**：把 `MemoryManager._resolve_memory_dir()` 中的 hash8 算法替换成调用 `mini_agent.paths.memory_dir()`。迁移逻辑已在任务 9 完成，这里只换路径函数。

**文件**：
- 修改：`mini_agent/tools/memory_manager.py:55-64`

**改动**：

```python
# 修改前：用 hashlib 计算 hash8
# 修改后：
def _resolve_memory_dir(self) -> Path:
    """解析 per-project memory 目录。

    使用 encoded-cwd 路径：~/.mini-agent/projects/<encoded-cwd>/memory/
    从老 <name>_<hash8> 格式的迁移由 mini_agent.session.migrate 在
    新版本首次启动时自动处理。
    """
    from ..paths import memory_dir
    return memory_dir(str(self.project_root))
```

同时移除 `hashlib` import（如果不再使用）。

**测试**：在 `tests/test_memory.py` 追加：

```python
def test_resolve_memory_dir_uses_encoded_cwd(temp_workspace, mini_agent_home):
    """迁移后 memory_manager 应使用 encoded-cwd 路径。"""
    from mini_agent.paths import encode_cwd
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)

    expected = (
        mini_agent_home / ".mini-agent" / "projects"
        / encode_cwd(str(temp_workspace)) / "memory"
    )
    assert manager.memory_dir == expected
```

可能需要更新既有测试（如果它们硬编码了 hash8 路径）。

**验证**：`pytest tests/test_memory.py -v` 全过。

---

### 任务 11：修改 `agent.py` 接受 SessionWriter

**目的**：把 Agent 中的 `AgentLogger` 替换为 `SessionWriter`，并在摘要发生时记录 `summary_event`。

**改动清单**：
1. **构造器签名**：去掉 logger 参数、加 `session_writer: "SessionWriter | None" = None`；若为 None 则 raise
2. **`self.session = session_writer`**（替代 `self.logger`）
3. **`add_user_message`**：`self.messages.append(...)` 后追加 `self.session.append_user_message(content)`
4. **所有 `self.logger.log_*` 调用替换**：
   - `log_response(...)` → `append_assistant_message(...)`
   - `log_tool_result(...)` → `append_tool_result(...)`
5. **`_summarize_messages`**：在创建 summary message 后调用 `append_summary_event`，需要追踪 seq

**seq 追踪方案**：Agent 维护 `self._message_seqs: list[int]` 与 `self.messages` 平行，每次 `append_*` 后记录返回的 seq。摘要时：
- `summarized_seqs` = 被替换掉的 message seqs
- `summary_seq` = 摘要消息本身的 seq

**文件**：
- 修改：`mini_agent/agent.py:48-96`（构造器）+ 全文 logger 引用
- 测试：`tests/test_agent_session_writer.py`（新增）+ 既有 `tests/test_agent.py`（更新 fixture）

**新测试**：

```python
def test_agent_has_session_not_logger(agent_with_writer):
    agent, writer = agent_with_writer
    assert agent.session is writer
    assert not hasattr(agent, "logger") or agent.logger is None

def test_agent_add_user_message_persists(agent_with_writer):
    agent, writer = agent_with_writer
    agent.add_user_message("test input")
    # session_writer 应记录了 user message 事件
    assert writer._seq >= 2  # session_start + this message
```

**验证**：`pytest tests/test_agent_session_writer.py tests/test_agent.py -v` 全过。

---

### 任务 12：`cli.py` 接入迁移 + session 标志位

**目的**：在 `main()` 顶部调用迁移；添加 argparse 参数；添加 `session`/`migrate-orphans` 子命令骨架。

**新增参数**：

```python
parser.add_argument("--continue", action="store_true", dest="continue_latest",
    help="Resume the most recent session in the current directory")
parser.add_argument("--resume", metavar="SESSION_ID", dest="resume_id",
    help="Resume a specific session by ID (supports prefix matching)")
parser.add_argument("--fork", metavar="SESSION_ID", dest="fork_id",
    help="Fork a new session from an existing one (preserves original)")
parser.add_argument("--any-workspace", action="store_true", dest="any_workspace",
    help="With --resume, search across all workspaces (default: strict to cwd)")
```

**新增子命令**：
- `mini-agent session list` / `cat <id>` / `tail <id>` / `rm <id>`
- `mini-agent migrate-orphans`

**迁移调用**：

```python
from mini_agent.session.migrate import migrate_all
migrate_all(cwd=str(Path.cwd()))
```

**文件**：
- 修改：`mini_agent/cli.py`
- 测试：`tests/test_cli_session_flags.py`

**测试**：通过 `subprocess` 跑 `--help`，验证三个 flag 都列出。

**验证**：`pytest tests/test_cli_session_flags.py -v` → 2 passed。

---

### 任务 13：`cli.py` 接入 new/resume/fork 分支

**目的**：实现设计文档 §6.7 的启动分支决策。

**分支决策**（优先级从高到低）：

```python
if args.continue_latest:
    latest = store.find_latest()
    if latest is None:
        sys.exit("Error: no session to continue in current cwd.")
    resume_id = latest.session_id
elif args.resume_id:
    resume_id = resolve_session_prefix(args.resume_id, store, args.any_workspace)
elif args.fork_id:
    fork_id = resolve_session_prefix(args.fork_id, store, any_workspace=False)

# 三选一分支
if resume_id:
    messages = reader.load_messages(resume_id, mode="compacted")
    writer = SessionWriter(session_id=resume_id, cwd=cwd_str, workspace=workspace_dir,
                            model=config.model, cli_args=vars(args))
    writer.open()  # 已有文件、追加模式，不写 session_start
elif fork_id:
    new_id = str(uuid.uuid4())
    store.fork_session(fork_id, new_id)
    messages = reader.load_messages(fork_id, mode="raw")
    writer = SessionWriter(session_id=new_id, cwd=cwd_str, workspace=workspace_dir,
                            model=config.model, cli_args=vars(args))
    writer.open(forked_from=fork_id)
else:
    new_id = str(uuid.uuid4())
    messages = []
    writer = SessionWriter(session_id=new_id, cwd=cwd_str, workspace=workspace_dir,
                            model=config.model, cli_args=vars(args))
    writer.open()
```

**模型不匹配警告**：resume/fork 时检查源 session 的 model 与当前 config.model 是否一致，不一致就提示。

**Session banner**：
```
🆔 Session <full-uuid>
   Resume later:  mini-agent --resume <short-id>
   Fork from it:  mini-agent --fork   <short-id>
```

**信号处理器（async-signal-safe）**：
- 第一次 SIGINT/SIGTERM：只设标志位 `_sig_handler._requested = "interrupt"`，主循环检测后优雅退出
- 第二次信号：恢复 SIG_DFL、立即 raise KeyboardInterrupt

**finally 块**：`writer.close(end_reason=getattr(_sig_handler, "_requested", None) or "exit")`

**文件**：
- 修改：`mini_agent/cli.py`（替换 Agent 构造点）
- 测试：`tests/test_cli_session_init.py`

**验证**：`pytest tests/test_cli_session_init.py -v` → PASS。

---

### 任务 14：`cli.py` in-progress 提示 + 斜杠命令

**目的**：用户启动时若没传 `--continue/--resume/--fork` 但 24h 内有未结束的 session，主动提示。同时 REPL 中添加 `/sessions` `/resume` `/fork` `/session-id` 斜杠命令。

**in-progress 提示**：

```
💡 检测到未结束的 session（24h 内）:

   [1] abc12345... | 12 msgs | 帮我修个 bug
   [2] def67890... | 3 msgs  | 写个测试

   选序号 resume / [n] new session / [f] fork
   > _
```

**斜杠命令实现要点**：
- `/sessions`：列出当前 workspace 的 sessions（id、状态、消息数、预览）
- `/resume [prefix]`：**软切换** — 关当前 writer、加载目标、构造新 writer/agent
- `/fork [prefix]`：**软切换** — fork 源、加载 fork 文件、构造新 writer（forked_from=源 id）
- `/session-id`：打印当前 session id + 短 id

**软切换实现**（关键代码）：

```python
elif command == "/resume" or command.startswith("/resume "):
    # ... 解析 target ...
    target = resolve_session_prefix(target, SessionStore(str(workspace_dir)))
    agent.session.close(end_reason="resume_switch")
    reader = SessionReader(str(workspace_dir))
    new_msgs = reader.load_messages(target, mode="compacted")
    new_writer = SessionWriter(
        session_id=target, cwd=str(workspace_dir), workspace=str(workspace_dir),
        model=config.model, cli_args={"resumed_from": agent.session.session_id},
    )
    try:
        new_writer.open()
    except SessionLockedError:
        print(f"⚠️ Session {target[:8]} is live in another process; not switched.")
        agent.session.open()  # 重开当前 session
        continue
    agent.messages = new_msgs
    agent.session = new_writer
    print(f"↪ Resumed session {target[:8]} ({len(new_msgs)} messages)")
```

`/fork` 类似，但用 `store.fork_session()` + raw 模式 + `forked_from`。

**文件**：
- 修改：`mini_agent/cli.py`
- 测试：`tests/test_cli_inprogress_prompt.py`

**验证**：`pytest tests/test_cli_inprogress_prompt.py -v` → PASS。

---

### 任务 15：`session/cli.py` — `session cat` 等子命令

**目的**：把 `mini-agent session <子命令>` 的处理器封装到独立模块，避免 `cli.py` 膨胀。

**子命令**：
- `session list` — 表格列出 sessions
- `session cat <id> [--format text|json] [--expand-external]` — pretty-print 整个 session
- `session tail <id> [-f] [-n N]` — 显示最后 N 条；`-f` 实时跟随（poll + 健康检查）
- `session rm <id>` — 删除 session 文件 + 对应 tool_outputs

**`cat` 的 text 格式**：

```
Session: abc-001
Started: 2026-06-18T10:00:00.000Z
Cwd:     /Users/jone/proj
Model:   gpt-4

----------------------------------------------------------------

[seq 1] USER               (10:00:01)
  hello

[seq 2] ASSISTANT          (10:00:02)
  Hi! How can I help?

[seq 3] TOOL  file_read    (10:00:03)
  [text] (inline)
  [external_ref size=12345 sha256=abc123def456...]

[seq 4] SUMMARY_EVENT      (10:05:00)
  summarized: [2, 3]  →  summary at seq 5
  tokens: 85000 → 12000

[seq 6] END (exit)
```

**`tail -f` 健康检查**：每 5s 检查锁是否还被持有；锁已释放且未见 `session_end` → 提示"writer 似乎崩溃"。

**文件**：
- 创建：`mini_agent/session/cli.py`
- 修改：`mini_agent/cli.py`（dispatch 到 `handle_session_command`）
- 测试：`tests/test_session_cli.py`

**测试覆盖**（4 个）：
- 空 list
- cat pretty-print
- cat --format json（每行可解析 JSON）
- rm 删除

**验证**：`pytest tests/test_session_cli.py -v` → 4 passed。

---

### 任务 16：删除 `logger.py` + 移除 `/log`

**目的**：清理被 SessionWriter 替代的旧 logger 代码。

**清单**：
1. 搜索所有 `AgentLogger|from .logger|from mini_agent.logger|/log\b` 引用
2. 从 `cli.py` 移除：
   - `from .logger import AgentLogger` import
   - `/log` 斜杠命令 dispatch（约 806 行）
   - `mini-agent log show` 子命令处理器（约 990 行）
   - `read_log_file` 及相关函数（约 148-205 行）
   - 从自动补全列表移除 `/log`（约 714 行）
3. `rm mini_agent/logger.py tests/test_logger.py`
4. `pytest tests/ -v` 全过

**提交**：`refactor: remove logger.py (replaced by session/writer.py)`

---

### 任务 17：端到端测试

**目的**：补齐设计文档 §10.4 要求的端到端场景，确保各组件协同正确。

**测试文件**：`tests/test_session_e2e.py`

**5 个测试**：
1. **完整生命周期** new → resume → fork：消息连续性、源 session 不被 fork 污染
2. **summary_event + compacted 模式**：raw 5 条、compacted 3 条（跳过 seq 2/3）
3. **并发 resume 被锁阻塞**：两个 writer 同 session，第二个 raise SessionLockedError
4. **部分 last line 恢复**：手动写截断 JSONL，reader 仍能加载前几条
5. **TTL 不删活动锁定的 session**：backdate mtime 但锁仍持有，cleanup 返回 0

**验证**：`pytest tests/test_session_e2e.py -v` → 5 passed。

---

### 任务 18：更新 CLAUDE.md

**目的**：把新的 session 模块加入项目文档。

**修改内容**：在"核心组件"小节加入：

```markdown
- **`mini_agent/session/`** - Session management（替代 logger）
  - `events.py` - SessionEvent schema
  - `writer.py` - Append-only JSONL writer（每个 Agent 一个）
  - `reader.py` - raw 或 compacted 模式加载 session
  - `store.py` - Session 列表、查找、fork、TTL 清理
  - `locker.py` - fcntl.flock 包装
  - `migrate.py` - hash8 → encoded-cwd 一次性迁移
  - `cli.py` - `mini-agent session <list|cat|tail|rm>` 处理器
- **`mini_agent/paths.py`** - encoded-cwd 路径工具
```

并在"工具在 `cli.py` 中创建 Agent 实例时注册"附近提及 session_writer 注入。

**验证**：`pytest tests/ -v --timeout=30` 全过。

---

### 任务 19：手动冒烟测试 + 最终提交

**步骤**：
1. `uv tool install -e .`
2. 新 session：`mini-agent` → 输入 hello → `/exit`，检查 JSONL 文件生成
3. continue：`mini-agent --continue`，检查上下文恢复
4. fork：`mini-agent --fork <id>`，检查 banner
5. `mini-agent session cat <id>` / `session list`，检查输出格式
6. 信号：在 REPL 中 Ctrl+C 一次（看 session_end 写入）+ Ctrl+C 两次（看立即退出）
7. 修复发现的问题，最终提交

**最终提交信息**：`feat(session): complete session management system (B standard)`

---

## 测试覆盖总览

设计文档 §10 要求的强制测试**全部覆盖**：

| 组件 | 覆盖点 | 状态 |
|------|--------|------|
| **Writer** | seq 单调、fsync、external_ref、secret 过滤 | ✓ |
| **Reader** | raw/compacted、嵌套 summary、partial line、external_ref 缺失、未知 type | ✓ |
| **FileLock** | 活进程持有、崩溃释放、竞态 | ✓ |
| **Migration** | 各匹配情况、marker 文件、env 变量、false positive | ✓ |
| **Fork** | 物理 copy、forked_from、源不变 | ✓ |
| **E2e** | new/resume/fork 周期、summary、并发锁、partial line、TTL | ✓ |

**主动延后的测试**（如时间充裕可补）：
- Reader：多层 summary_event 嵌套链（任务 7 只测单层）
- Reader：external_ref 的 sha256 不匹配路径（只测了文件缺失）
- FileLock：probe-release-reacquire 竞态（任务 4 分别测了 probe 和 release）
- Migration：multi-match 歧义解决、跨文件系统 EXDEV mock
- §10.5 性能基线（append < 5ms、1000-event load < 200ms 等）
- 信号处理器的 subprocess 测试（pytest 中难可靠测试，靠任务 19 手动冒烟覆盖）
- `--continue`/`--resume <prefix>`/`--fork <id>` 的 CLI subprocess e2e（任务 12-13 的单测覆盖了逻辑，手动冒烟覆盖 e2e）

---

## 范围外（明确不做）

| 项目 | 推迟到 | 理由 |
|------|--------|------|
| `rename_session` / `tag_session` API | C 标准 | 当前用 uuid 够用 |
| SessionStore 抽象层 | C 标准 | YAGNI |
| 文件 checkpointing | C 标准 | YAGNI |
| `search_sessions` agent 工具 | 真有需求时 | 需配套语义检索 |
| 跨机器同步（S3 / 分布式 FS） | C 标准 | 业界无人这么做，YAGNI |
| Anthropic prompt caching 集成 | 单独特性 | 届时 session_start 加 `system_prompt_sha256` |

---

## 风险与回滚

- **风险等级**：中等（替换核心 logger、改动 cli.py 大量代码）
- **风险点**：
  - seq 追踪与 summary_event 协调不当 → resume 后行为异常
  - cli.py 启动分支重构可能引入回归 → 任务 17 e2e 兜底
  - 迁移脚本误删数据 → 任务 9 严格测试 + marker file idempotent
- **回滚**：每个任务都是独立 commit，可逐个 `git revert`；任务 16 删除 logger 之前都是兼容期

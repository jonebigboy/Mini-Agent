# 安全确认机制 stdin 冲突导致 CLI 无响应

> 日期: 2026-06-10
> 影响版本: fsw 分支 (security middleware feature)
> 修复提交: 待提交

---

## 1. 问题现象

用户在使用安全 sandbox 模块时遇到两个问题：

1. **无法确认危险命令**：当 Agent 尝试执行需要确认的命令（如 `sudo`）时，弹出确认提示 `[y] 执行一次 [n] 拒绝 [a] 始终允许此类命令 >`，但输入 `y` + 回车后没有任何反应，命令不会被放行
2. **CLI 卡死**：按 2 次回车后，CLI 进入完全无响应状态，既无法退出也无法继续操作，只能强制终止进程

---

## 2. 根因分析

### 2.1 调用链

```
cli.py: session.prompt_async()     ← 用户输入指令
  → cli.py: esc_thread.start()     ← 启动 Esc 监听线程
    → agent.py: agent.run()        ← Agent 执行循环
      → bash_tool.py: execute()    ← 工具执行
        → middleware.py: check_command()  ← 安全检查
          → confirm.py: input()    ← ❌ 在此调用 input() 等待用户输入
```

### 2.2 stdin 消费者分析

CLI 运行时涉及 stdin 的有三个组件：

| 组件 | 活跃时段 | stdin 行为 |
|------|----------|-----------|
| `prompt_toolkit` | 用户输入指令时 | 持有 stdin，正常管理 |
| Esc 监听线程 (`cli.py:796-817`) | Agent 执行期间 | **贪婪消费所有字符** |
| `input()` (`confirm.py:31`) | 安全确认时 | 尝试读取一行输入 |

关键时序：**Esc 监听线程和安全确认的 `input()` 在同一时段活跃，争抢 stdin。**

### 2.3 Esc 监听线程如何偷字符

```python
# cli.py:796-817 — Esc 监听线程（简化）
def esc_key_listener():
    tty.setcbreak(fd)              # 将终端设为 cbreak（原始）模式
    while not esc_listener_stop.is_set():
        rlist, _, _ = select.select([sys.stdin], [], [], 0.05)  # 每 50ms 轮询
        if rlist:
            char = sys.stdin.read(1)   # 消费一个字符
            if char == "\x1b":         # 只有 Esc 触发取消
                cancel_event.set()
                break
            # ← 其他字符被静默丢弃！
```

**问题**：该线程在 cbreak 模式下以 50ms 间隔轮询 stdin。`sys.stdin.read(1)` 是不可逆的 — 读到的字符无法放回。只有 `\x1b`（Esc）会被处理，**所有其他字符（包括 `y`、回车）都被丢弃**。

### 2.4 Bug 1 的原因：`y` 被偷

```
用户输入 'y' → stdin 有数据
         ↓
Esc 线程 select() 返回 → read(1) 拿到 'y' → 'y' ≠ Esc → 丢弃
         ↓
input() 永远等不到 'y' → 无响应
```

### 2.5 Bug 2 的原因：终端状态混乱

Esc 线程通过 `tty.setcbreak(fd)` 将终端设为 cbreak 模式（无行缓冲、无行编辑）。`input()` 底层调用 `sys.stdin.readline()`，期望终端在正常（canonical）模式下工作。两者的模式不兼容：

- cbreak 模式：字符立即可用，无行缓冲，退格键不生效
- `input()` 期望的 canonical 模式：字符缓冲直到回车，支持行编辑

当 `input()` 在 cbreak 模式下等待输入时，回车也被 Esc 线程偷走。多次无效输入后终端状态不可恢复，导致 CLI 卡死。

### 2.6 为什么平时对话不受影响

Esc 监听线程只在 Agent 执行期间（`cli.py:820-841`）存活：

```
① session.prompt_async()   ← prompt_toolkit 管理 stdin，Esc 线程还没启动
② 用户输入命令 + 回车
③ esc_thread.start()        ← Esc 线程才启动
④ agent.run()               ← Agent 执行
⑤ esc_thread.join()         ← Esc 线程停止
⑥ 回到 ①
```

平时输入命令时处于 ① 阶段，stdin 由 `prompt_toolkit` 管理，Esc 线程还没启动。只有当安全确认发生在 ④ 的内部时，才会出现 `input()` 和 Esc 线程同时争抢 stdin 的问题。

---

## 3. 方案对比

### 方案 A：暂停 Esc 线程 + 恢复终端模式

在安全确认获取输入前，暂停 Esc 监听线程并恢复终端正常模式，确认完成后恢复。

- 优点：改动最小，只需添加 hooks 机制
- 缺点：需要管理终端设置的保存/恢复时序，存在竞态条件风险；hook 需穿透多层（CLI → Agent → Tool → Middleware → Confirm）

### 方案 B（最终选择）：将确认移到 CLI 层

工具执行不再内部调 `input()`，改为抛出异常 → Agent 暂停 → CLI 用 `prompt_toolkit` 处理确认 → Agent 恢复。

- 优点：
  - stdin 归属权清晰：输入永远由 CLI 主循环的 `prompt_toolkit` 处理
  - 从根本上避免 stdin 竞争，不需要管理终端设置
  - 职责分离清晰：安全层只判断，CLI 层负责交互
  - Agent 暂停/恢复机制可复用于其他需要用户输入的场景
- 缺点：改动范围较大，需修改 middleware → bash_tool → agent → cli 四层

### 方案 C：用 asyncio 替代 Esc 监听线程

将 Esc 检测从线程改为 `loop.add_reader()` 异步监听。

- 优点：从架构上消除多线程竞争
- 缺点：需要重构 Esc 监听机制，改动量中等

### 方案 D：Esc 线程缓冲非 Esc 字符

将读到的非 Esc 字符存入队列，`input()` 改为从队列读取。

- 优点：Esc 线程不用暂停
- 缺点：需包装 stdin，侵入性强；cbreak 模式下 `input()` 的行编辑仍有问题

### 选择方案 B 的原因

方案 B 从架构层面解决问题。它的核心思路是 **stdin 在任意时刻只有一个消费者** — 要么是 `prompt_toolkit`（用户输入阶段），要么是 Esc 线程（Agent 执行阶段），永远不会出现两者同时争抢的情况。安全确认被放在了 `prompt_toolkit` 阶段处理，Esc 线程在确认前会被停止。

---

## 4. 方案设计

### 4.1 核心设计思路

方案 B 的核心原则：**stdin 在任意时刻只有一个消费者**。

原始架构中 `input()` 和 Esc 线程同时运行，导致 stdin 竞争。修复后的架构保证：

```
时刻 1: prompt_toolkit 持有 stdin   ← 用户输入 / 安全确认
时刻 2: Esc 线程持有 stdin          ← Agent 执行中监听取消
```

这两个时刻永远不会重叠。当需要安全确认时，Agent 暂停执行，Esc 线程停止，控制权回到 CLI 的 `prompt_toolkit`。

### 4.2 分层职责划分

修复前后的职责对比：

| 层级 | 修复前 | 修复后 |
|------|--------|--------|
| **SecurityMiddleware** | 判断风险 + 调用 `input()` 获取确认 | **只判断风险**，返回 `needs_confirmation=True` |
| **BashTool** | 不知道确认的存在 | 检测到确认需求时抛出 `ConfirmationRequired` 异常 |
| **Agent** | 不知道确认的存在 | 捕获异常、暂停执行、等待 CLI 唤醒 |
| **CLI** | 不知道确认的存在 | 用 `prompt_toolkit` 弹确认提示，唤醒 Agent |

每一层只做一件事，不越权。

### 4.3 暂停/恢复机制设计

Agent 使用 `asyncio.Event` 实现暂停/恢复：

```
Agent 执行中                          CLI 轮询中
     │                                    │
     ├─ tool.execute()                    │
     │   └─ security.check_command()      │
     │       └─ 返回 needs_confirmation    │
     │   └─ raise ConfirmationRequired     │
     │                                    │
     ├─ 捕获异常                           │
     ├─ 设置 confirmation_request = req    │  ← CLI 检测到
     ├─ _confirmation_event.clear()        │
     ├─ await _confirmation_event.wait() ──┤  ← 暂停，让出控制权
     │                                    │
     │                              ┌─────┤
     │                              │ 停止 Esc 线程
     │                              │ prompt_toolkit 弹提示
     │                              │ 用户输入 y/n/a
     │                              │ 调用 resolve_confirmation()
     │                              └─────┤
     │                                    │
     ├─ _confirmation_event 被唤醒 ◀──────┤
     ├─ 读取 _confirmation_result         │
     ├─ 调用 security.apply_confirmation()│
     ├─ 重新执行 tool.execute()            │
     │   └─ check_command() 发现已确认 → 放行
     │   └─ 执行命令，返回结果              │
     │                                    │
     ├─ 继续执行后续 tool calls             │
     │                              ┌─────┤
     │                              │ 重启 Esc 线程
     │                              └─────┤
```

关键点：Agent 暂停时，asyncio 事件循环仍在运行，CLI 的轮询代码可以继续执行。`await Event.wait()` 只是挂起当前协程，不会阻塞整个事件循环。

---

## 5. 逐步实现详解

### Step 1: models.py — 添加确认信号字段

在 `SecurityDecision` 中增加 `needs_confirmation` 字段，让 middleware 可以告诉上层"这个命令需要确认"。

```python
# mini_agent/security/models.py
@dataclass
class SecurityDecision:
    allowed: bool
    reason: str = ""
    command: str = ""
    env: dict = field(default_factory=dict)
    needs_confirmation: bool = False   # ← 新增
```

这是整个修复的基础 — 有了这个字段，middleware 就不需要自己调 `input()` 了，只需返回一个声明式的判断结果。

### Step 2: middleware.py — 不再调 input()，改为返回确认信号

修改 `check_command()` 方法的 `RiskLevel.CONFIRM` 分支：

```python
# 修复前：直接调 input()，在 Agent 执行线程中阻塞
if risk == RiskLevel.CONFIRM:
    result = await self.confirmation.confirm(command, reason)  # ← 调 input()
    if result == ConfirmResult.DENY:
        return SecurityDecision(allowed=False, reason="用户拒绝执行")

# 修复后：返回声明式的确认请求
if risk == RiskLevel.CONFIRM:
    return SecurityDecision(
        allowed=False,
        reason=reason,
        command=command,
        needs_confirmation=True,   # ← 告诉上层"需要确认"
    )
```

新增 `apply_confirmation()` 方法，供 CLI 确认后调用：

```python
def apply_confirmation(self, command: str, always: bool = False) -> SecurityDecision:
    if always:
        self.confirmation.add_permanent_rule(command)  # 持久化到文件
    else:
        self._session_allowed.add(command)              # 仅本次放行
    return SecurityDecision(allowed=True, command=command, env=self._clean_env)
```

同时修改 `check_command()` 优先检查 `_session_allowed`：

```python
if risk == RiskLevel.CONFIRM:
    # 本次会话已确认过的命令直接放行
    if command in self._session_allowed:
        self._session_allowed.discard(command)
        return SecurityDecision(allowed=True, command=command, env=self._clean_env)
    # ... 否则返回 needs_confirmation
```

这个 `_session_allowed` 集合是 Code Review 时发现的关键修复 — 没有它，用户选 `y`（执行一次）后重新执行 `tool.execute()` 会再次触发 `ConfirmationRequired`，导致无限循环。

### Step 3: base.py — 定义共享异常

```python
# mini_agent/tools/base.py
class ConfirmationRequired(Exception):
    """Raised when a tool needs user confirmation before executing."""

    def __init__(self, command: str, reason: str):
        self.command = command   # 需要确认的命令
        self.reason = reason     # 为什么需要确认
        super().__init__(f"需要用户确认: {command} ({reason})")
```

放在 `tools/base.py` 而非 `bash_tool.py`，因为其他工具（如 file_tools）未来也可能需要确认机制。避免 agent 核心模块依赖具体的工具实现。

### Step 4: bash_tool.py — 检测确认需求并抛异常

修改 `BashTool.execute()` 的安全检查逻辑：

```python
# 修复前：所有不通过的情况都返回错误
if not decision.allowed:
    return BashOutputResult(
        success=False,
        error=f"命令被安全策略拒绝: {decision.reason}",
        ...
    )

# 修复后：区分"需要确认"和"拒绝"两种情况
if not decision.allowed:
    if decision.needs_confirmation:
        raise ConfirmationRequired(command, decision.reason)  # ← 抛异常
    return BashOutputResult(
        success=False,
        error=f"命令被安全策略拒绝: {decision.reason}",
        ...
    )
```

用异常而非返回值的原因：返回值会被当作正常的工具结果加入消息历史，LLM 会看到"被拒绝"并尝试其他方式。而异常可以被 Agent 的执行循环捕获，触发暂停/恢复流程，不进入消息历史。

### Step 5: agent.py — 暂停/恢复机制

在 `Agent.__init__` 中初始化状态：

```python
# 确认暂停/恢复状态（由 CLI 层使用）
self.confirmation_request: ConfirmationRequired | None = None
self._confirmation_event = asyncio.Event()
self._confirmation_result: str | None = None   # "yes", "always", 或 None(拒绝)
```

修改工具执行循环，捕获 `ConfirmationRequired`：

```python
# 修复前：所有异常统一处理
try:
    result = await tool.execute(**arguments)
except Exception as e:
    result = ToolResult(success=False, error=...)

# 修复后：ConfirmationRequired 单独处理
try:
    result = await tool.execute(**arguments)
except ConfirmationRequired as e:
    result = await self._wait_for_confirmation(e, tool, arguments)  # ← 暂停等待
except Exception as e:
    result = ToolResult(success=False, error=...)
```

`_wait_for_confirmation()` 方法的实现：

```python
async def _wait_for_confirmation(self, req, tool, arguments):
    # 1. 暴露确认请求，让 CLI 能检测到
    self.confirmation_request = req
    self._confirmation_event.clear()

    # 2. 暂停，等待 CLI 调用 resolve_confirmation()
    await self._confirmation_event.wait()

    # 3. 读取结果
    result_text = self._confirmation_result
    self.confirmation_request = None
    self._confirmation_result = None

    # 4a. 用户拒绝 → 返回错误
    if result_text is None:
        return ToolResult(success=False, error=f"用户拒绝执行: {req.command}")

    # 4b. 用户确认 → 通知安全中间件 → 重新执行工具
    if hasattr(tool, "security") and tool.security:
        tool.security.apply_confirmation(req.command, always=(result_text == "always"))

    return await tool.execute(**arguments)
```

暴露给 CLI 的唤醒接口：

```python
def resolve_confirmation(self, result: str | None):
    """由 CLI 调用，传入用户确认结果。"""
    self._confirmation_result = result
    self._confirmation_event.set()   # 唤醒 Agent
```

### Step 6: cli.py — CLI 层处理确认

修改 Agent 执行期间的轮询循环。修复前只是检测 Esc 取消：

```python
# 修复前
while not agent_task.done():
    if esc_cancelled[0]:
        cancel_event.set()
    await asyncio.sleep(0.1)
```

修复后增加确认请求检测：

```python
# 修复后
while not agent_task.done():
    if esc_cancelled[0]:
        cancel_event.set()

    # 检测 Agent 是否因确认而暂停
    if agent.confirmation_request is not None:
        # ① 停止 Esc 线程，释放 stdin
        esc_listener_stop.set()
        esc_thread.join(timeout=0.3)

        # ② 用 prompt_toolkit 弹确认提示
        req = agent.confirmation_request
        print(f"\n⚠️  危险命令检测: {req.command}")
        print(f"原因: {req.reason}")
        choice = await session.prompt_async("[y] 执行一次  [n] 拒绝  [a] 始终允许 > ")

        # ③ 根据用户选择唤醒 Agent
        choice = choice.strip().lower()
        if choice == "a":
            agent.resolve_confirmation("always")
        elif choice == "y":
            agent.resolve_confirmation("yes")
        else:
            agent.resolve_confirmation(None)   # 拒绝

        # ④ 重启 Esc 线程
        esc_listener_stop = threading.Event()
        esc_thread = threading.Thread(target=esc_key_listener, daemon=True)
        esc_thread.start()

    await asyncio.sleep(0.1)
```

这里的关键时序保证：

1. **Esc 线程先停止** → `join(0.3)` 确保它不再读 stdin
2. **prompt_toolkit 弹提示** → 此时 stdin 无竞争者，用户输入不会丢失
3. **唤醒 Agent** → `resolve_confirmation()` 设置结果并 `set()` Event
4. **重启 Esc 线程** → Agent 恢复执行后，Esc 监听也恢复

### Step 7: confirm.py — 清理死代码

`confirm()` 方法和 `ConfirmResult` 枚举不再被任何地方调用（middleware 已经不调 `input()` 了），从 `confirm.py` 和测试中移除，只保留 `UserConfirmation` 的规则持久化功能（`add_permanent_rule`、`_load_rules`、`_save_rules`）。

---

## 6. Code Review 发现的额外问题

### 6.1 "执行一次" 无限循环（Critical）

**问题**：用户选 `y` 后，`apply_confirmation(always=False)` 没有记录任何信息。重新执行 `tool.execute()` 时，`check_command()` 再次判定命令需要确认，再次抛出 `ConfirmationRequired`，形成无限循环。

```
用户选 y → apply_confirmation(always=False) → 什么都没记录
  → tool.execute() → check_command() → 还是需要确认
  → raise ConfirmationRequired → _wait_for_confirmation
  → CLI 又弹确认 → 用户又选 y → 又什么都没记录 → 无限循环
```

**修复**：在 `SecurityMiddleware` 增加 `_session_allowed: set[str]` 集合：

- `apply_confirmation(always=False)` 将命令加入集合
- `check_command()` 在进入 CONFIRM 分支时，优先检查集合，命中则放行并移除
- `apply_confirmation(always=True)` 仍然持久化到文件

三种确认路径的行为：

| 用户选择 | 行为 | 下次执行相同命令 |
|----------|------|-----------------|
| `y` | 加入 `_session_allowed`，仅放行一次 | 需要再次确认 |
| `a` | 持久化到 `security_rules.json` | 永久放行 |
| `n` / Ctrl+C | 不记录 | 需要再次确认 |

### 6.2 ConfirmationRequired 位置不当（Important）

初始实现将 `ConfirmationRequired` 放在 `bash_tool.py`，而 `agent.py` 需要导入它，造成 agent 核心依赖具体工具。已移至 `tools/base.py` 共享位置。

### 6.3 死代码 ConfirmResult（Minor）

`ConfirmResult` 枚举和 `confirm()` 方法不再被调用，已清理。

---

## 7. 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `security/models.py` | `SecurityDecision` 增加 `needs_confirmation: bool` 字段 |
| `security/middleware.py` | `check_command()` 返回 `needs_confirmation=True` 而非调 `input()`；新增 `apply_confirmation()` 和 `_session_allowed` |
| `security/confirm.py` | 移除 `ConfirmResult` 和 `confirm()`，保留规则持久化 |
| `tools/base.py` | 新增 `ConfirmationRequired` 异常 |
| `tools/bash_tool.py` | 安全检查抛出 `ConfirmationRequired` 而非返回错误 |
| `agent.py` | 新增 `_wait_for_confirmation()`、`resolve_confirmation()`；捕获 `ConfirmationRequired` |
| `cli.py` | 轮询循环检测确认请求，用 `prompt_toolkit` 处理 |
| `tests/test_security_confirm.py` | 移除已废弃的 `ConfirmResult` 测试 |

---

## 8. 待解决问题

### 8.1 安全规则的作用域

当前 `rules_file` 默认值为 `~/.mini-agent/security_rules.json`（全局路径），选 `a`（始终允许）的规则在所有项目中生效。可能需要改为按项目（workspace）隔离，将规则文件存储在 `{workspace}/.mini-agent/security_rules.json`。

### 8.2 Esc 键在确认提示中的行为

确认提示使用 `prompt_toolkit`，Esc 键被 `prompt_toolkit` 用作转义序列前缀（如方向键），不会触发"拒绝"。用户按 Esc 想拒绝时实际是无效操作。Ctrl+C 可以正确触发拒绝。

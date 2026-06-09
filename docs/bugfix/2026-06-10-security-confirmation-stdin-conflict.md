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

## 4. 修复实现

### 4.1 数据流（修复后）

```
BashTool → 检测到 needs_confirmation → 抛出 ConfirmationRequired 异常
  ↓
Agent 捕获异常 → 设置 confirmation_request → await Event 暂停
  ↓
CLI 轮询发现 confirmation_request
  → 停止 Esc 线程（释放 stdin）
  → prompt_toolkit 弹确认提示（用户输入 y/n/a）
  → 调用 agent.resolve_confirmation()
  ↓
Agent 唤醒 → 调用 security.apply_confirmation() → 重新执行该 tool call
  → 重启 Esc 线程
```

### 4.2 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `security/models.py` | `SecurityDecision` 增加 `needs_confirmation: bool` 字段 |
| `security/middleware.py` | `check_command()` 不再调 `input()`，需要确认时返回 `needs_confirmation=True`；新增 `apply_confirmation()` 供确认后调用；新增 `_session_allowed` 集合防止 "执行一次" 后无限循环 |
| `security/confirm.py` | 移除 `ConfirmResult` 枚举和 `confirm()` 方法（不再需要），保留 `UserConfirmation` 的规则持久化功能 |
| `tools/base.py` | 新增 `ConfirmationRequired` 异常类（共享位置） |
| `tools/bash_tool.py` | 安全检查检测到 `needs_confirmation` 时抛出 `ConfirmationRequired` 而非返回错误 |
| `agent.py` | 新增 `_wait_for_confirmation()` 暂停机制和 `resolve_confirmation()` 唤醒接口；工具执行循环捕获 `ConfirmationRequired` |
| `cli.py` | 轮询循环中检测 `agent.confirmation_request`，停止 Esc 线程后用 `prompt_toolkit` 弹确认提示，用户响应后唤醒 Agent 并重启 Esc 线程 |
| `tests/test_security_confirm.py` | 移除已废弃的 `ConfirmResult` 测试 |

### 4.3 Code Review 发现的额外问题

在实现后的 Code Review 中发现了一个 **Critical bug**：

**"执行一次"（y）无限循环**：用户选 `y` 后，`apply_confirmation(always=False)` 没有记录任何信息，重新执行 `tool.execute()` 时 `check_command()` 再次判定为 `needs_confirmation`，再次抛出异常，形成无限循环。

修复：在 `SecurityMiddleware` 增加 `_session_allowed: set[str]` 集合：
- `apply_confirmation(always=False)` 将命令加入集合
- `check_command()` 优先检查集合，命中则放行并移除
- `apply_confirmation(always=True)` 仍然持久化到文件

---

## 5. 待解决问题

### 5.1 安全规则的作用域

当前 `rules_file` 默认值为 `~/.mini-agent/security_rules.json`（全局路径），意味着选 `a`（始终允许）的规则在所有项目中生效。可能需要改为按项目（workspace）隔离，将规则文件存储在 `{workspace}/.mini-agent/security_rules.json`。

### 5.2 Esc 键在确认提示中的行为

确认提示使用 `prompt_toolkit`，Esc 键被 `prompt_toolkit` 用作转义序列前缀（如方向键），不会触发 "拒绝"。用户如果按 Esc 想拒绝，实际效果是无效操作。Ctrl+C 可以正确触发拒绝。

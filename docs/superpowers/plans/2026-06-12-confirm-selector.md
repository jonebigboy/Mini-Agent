# 交互式确认选择器 实现计划

> **给执行代理：** 必须使用的子技能：superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，逐步执行本计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 将基于文本输入的 y/n/a 确认提示替换为交互式上下键选择菜单，新增「输入反馈指令」选项，让用户可以输入文字来引导 LLM 的下一步行为。

**架构：** 使用 `prompt_toolkit.Application` 创建 `ConfirmSelector` 组件，包含自定义按键绑定和格式化文本渲染。选择器返回 `ConfirmResult`（包含 action 和可选 feedback）。Agent 的确认流程扩展为通过 `ToolResult.error` 传递反馈文字，该文字会自动进入 LLM 下一轮对话的上下文中。

**技术栈：** Python、prompt_toolkit（已有依赖）、asyncio

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `mini_agent/ui/__init__.py` | 新建 | 包初始化 |
| `mini_agent/ui/confirm_selector.py` | 新建 | `ConfirmResult` 模型 + `ConfirmSelector` 组件 |
| `mini_agent/agent.py:94` | 修改 | 添加 `_confirmation_feedback` 初始化字段 |
| `mini_agent/agent.py:534-584` | 修改 | 在 `_wait_for_confirmation` + `resolve_confirmation` 中处理反馈 |
| `mini_agent/cli.py:838-856` | 修改 | 用 `ConfirmSelector` 替换文本提示 |
| `tests/test_confirm_selector.py` | 新建 | 结果模型和 Agent 反馈流程的单元测试 |

---

### 任务 1：创建 `ui` 包和 `ConfirmResult` 模型

**涉及文件：**
- 新建：`mini_agent/ui/__init__.py`
- 新建：`mini_agent/ui/confirm_selector.py`
- 新建：`tests/test_confirm_selector.py`

- [ ] **步骤 1：编写 `ConfirmResult` 的测试**

```python
# tests/test_confirm_selector.py
"""Tests for the confirm selector component."""

from mini_agent.ui.confirm_selector import ConfirmResult


def test_confirm_result_yes_action():
    result = ConfirmResult(action="yes")
    assert result.action == "yes"
    assert result.feedback is None


def test_confirm_result_feedback_action():
    result = ConfirmResult(action="feedback", feedback="use brew instead")
    assert result.action == "feedback"
    assert result.feedback == "use brew instead"


def test_confirm_result_no_action():
    result = ConfirmResult(action="no")
    assert result.action == "no"
    assert result.feedback is None


def test_confirm_result_always_action():
    result = ConfirmResult(action="always")
    assert result.action == "always"
    assert result.feedback is None
```

- [ ] **步骤 2：运行测试，确认失败**

执行：`cd /Users/jone/Desktop/codespace/Mini-Agent && uv run pytest tests/test_confirm_selector.py -v`
预期：FAIL — `ModuleNotFoundError: No module named 'mini_agent.ui'`

- [ ] **步骤 3：创建 `ui` 包和 `ConfirmResult` 实现**

```python
# mini_agent/ui/__init__.py
```

```python
# mini_agent/ui/confirm_selector.py
"""交互式确认选择器，支持上下键导航。"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class ConfirmResult:
    """用户确认交互的结果。

    Attributes:
        action: 用户的选择 —— "yes"、"no"、"always" 或 "feedback"。
        feedback: 当 action 为 "feedback" 时的自由文本反馈，其他情况为 None。
    """

    action: Literal["yes", "no", "always", "feedback"]
    feedback: str | None = None
```

- [ ] **步骤 4：运行测试，确认通过**

执行：`uv run pytest tests/test_confirm_selector.py -v`
预期：4 个测试 PASS

- [ ] **步骤 5：提交**

```bash
git add mini_agent/ui/__init__.py mini_agent/ui/confirm_selector.py tests/test_confirm_selector.py
git commit -m "feat: add ConfirmResult model for interactive confirmation"
```

---

### 任务 2：实现 `ConfirmSelector` 组件

**涉及文件：**
- 修改：`mini_agent/ui/confirm_selector.py`

- [ ] **步骤 1：编写选择器的测试**

添加到 `tests/test_confirm_selector.py`：

```python
from mini_agent.ui.confirm_selector import ConfirmSelector


def test_selector_options_structure():
    """验证 OPTIONS 元组结构正确：(key, action, label)。"""
    selector = ConfirmSelector(command="sudo apt update", reason="命令需要确认")
    assert len(selector.OPTIONS) == 4
    keys = [opt[0] for opt in selector.OPTIONS]
    assert keys == ["y", "n", "a", "e"]
    actions = [opt[1] for opt in selector.OPTIONS]
    assert "yes" in actions
    assert "no" in actions
    assert "always" in actions
    assert "feedback" in actions


def test_selector_initial_selected():
    selector = ConfirmSelector(command="test", reason="test")
    assert selector.selected == 0


def test_selector_render_contains_all_options():
    selector = ConfirmSelector(command="sudo apt update", reason="命令需要确认")
    rendered = selector._render()
    text = "".join(fragment[1] for fragment in rendered)
    assert "sudo apt update" in text
    assert "命令需要确认" in text
    for key, _, label in selector.OPTIONS:
        assert key in text
        assert label in text


def test_selector_render_highlights_selected():
    selector = ConfirmSelector(command="test", reason="test")
    selector.selected = 1  # 选中"拒绝"
    rendered = selector._render()
    # 验证被选中的"拒绝"行有"selected"样式
    for style, text in rendered:
        if "▸" in text and "[n]" in text:
            assert "class:selected" in style
            break
    else:
        raise AssertionError("Expected selected indicator for option at index 1")
```

- [ ] **步骤 2：运行测试，确认失败**

执行：`uv run pytest tests/test_confirm_selector.py -v`
预期：FAIL — `ImportError` 或 `AttributeError`（`ConfirmSelector` 不存在）

- [ ] **步骤 3：实现 `ConfirmSelector`**

追加到 `mini_agent/ui/confirm_selector.py`（在 `ConfirmResult` 类之后）：

```python
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.styles import Style as PromptStyle


class ConfirmSelector:
    """交互式确认选择器，支持上下键导航。

    渲染选项菜单（yes/no/always/feedback），用户可以通过上下方向键
    （或 j/k vim 绑定）导航，回车确认选择。也可以直接按选项键
    （y/n/a/e）快速选择。

    当选择"输入反馈指令"时，会弹出二级提示框收集用户文本输入。
    """

    OPTIONS = [
        ("y", "yes", "执行一次"),
        ("n", "no", "拒绝"),
        ("a", "always", "始终允许此类命令"),
        ("e", "feedback", "输入反馈指令..."),
    ]

    def __init__(self, command: str, reason: str):
        self.command = command
        self.reason = reason
        self.selected = 0

    def _render(self):
        """返回菜单的格式化文本片段列表。"""
        lines = []
        lines.append(("class:warning", "\n⚠️  危险命令检测: "))
        lines.append(("class:command", self.command))
        lines.append(("", f"\n原因: {self.reason}\n\n"))

        for i, (key, _, label) in enumerate(self.OPTIONS):
            if i == self.selected:
                lines.append(("class:selected", f"  ▸ [{key}] {label}"))
            else:
                lines.append(("class:normal", f"    [{key}] {label}"))
            lines.append(("", "\n"))

        return lines

    def _build_key_bindings(self):
        """创建导航和选择的按键绑定。"""
        kb = KeyBindings()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            self.selected = (self.selected - 1) % len(self.OPTIONS)
            event.app.invalidate()

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            self.selected = (self.selected + 1) % len(self.OPTIONS)
            event.app.invalidate()

        @kb.add("enter")
        def _enter(event):
            _, action, _ = self.OPTIONS[self.selected]
            event.app.exit(result=action)

        # 快捷键直接选择
        for key_char, action, _ in self.OPTIONS:

            def _make_handler(a):
                def handler(event):
                    event.app.exit(result=a)
                return handler

            kb.add(key_char)(_make_handler(action))

        @kb.add("c-c")
        @kb.add("escape")
        def _cancel(event):
            event.app.exit(result="no")

        return kb

    async def select(self) -> ConfirmResult:
        """显示选择器并等待用户操作，返回选择结果。"""
        from prompt_toolkit.layout import FormattedTextControl

        control = FormattedTextControl(text=self._render)
        layout = Layout(HSplit([Window(content=control)]))

        style = PromptStyle.from_dict(
            {
                "warning": "#ffaa00 bold",
                "command": "#ff4444 bold",
                "selected": "reverse bold",
                "normal": "",
            }
        )

        app = Application(
            layout=layout,
            key_bindings=self._build_key_bindings(),
            style=style,
            full_screen=False,
        )

        action = await app.run_async()

        if action is None:
            action = "no"

        if action == "feedback":
            from prompt_toolkit import PromptSession

            session = PromptSession()
            try:
                feedback = await session.prompt_async("  请输入反馈指令:\n  > ")
            except (KeyboardInterrupt, EOFError):
                return ConfirmResult(action="no")
            if not feedback.strip():
                return ConfirmResult(action="no")
            return ConfirmResult(action="feedback", feedback=feedback.strip())

        return ConfirmResult(action=action)
```

- [ ] **步骤 4：运行测试，确认通过**

执行：`uv run pytest tests/test_confirm_selector.py -v`
预期：8 个测试 PASS（4 个 ConfirmResult + 4 个 ConfirmSelector）

- [ ] **步骤 5：提交**

```bash
git add mini_agent/ui/confirm_selector.py tests/test_confirm_selector.py
git commit -m "feat: implement ConfirmSelector with prompt_toolkit"
```

---

### 任务 3：为 Agent 添加反馈支持

**涉及文件：**
- 修改：`mini_agent/agent.py:94`（添加 `_confirmation_feedback` 字段）
- 修改：`mini_agent/agent.py:534-584`（在确认方法中处理反馈）
- 修改：`tests/test_confirm_selector.py`（添加 Agent 反馈测试）

- [ ] **步骤 1：编写 Agent 反馈流程的测试**

添加到 `tests/test_confirm_selector.py`：

```python
import asyncio

import pytest

from mini_agent.agent import Agent
from mini_agent.llm.llm_wrapper import LLMClient
from mini_agent.tools.base import ConfirmationRequired, ToolResult


@pytest.mark.asyncio
async def test_resolve_confirmation_with_feedback():
    """当 resolve_confirmation 传入 feedback 时，拒绝的 ToolResult
    应包含反馈文字。"""
    from unittest.mock import AsyncMock, MagicMock

    llm = MagicMock(spec=LLMClient)
    llm.generate = AsyncMock()
    agent = Agent(
        llm=llm,
        system_prompt="test",
        workspace_dir="/tmp",
        max_steps=1,
    )

    req = ConfirmationRequired(command="sudo apt update", reason="命令需要确认")
    agent.confirmation_request = req
    agent._confirmation_event.clear()

    async def resolve_after_delay():
        await asyncio.sleep(0.05)
        agent.resolve_confirmation(None, feedback="用 brew 替代")

    asyncio.create_task(resolve_after_delay())

    mock_tool = MagicMock()
    result = await agent._wait_for_confirmation(req, mock_tool, {})

    assert result.success is False
    assert "用 brew 替代" in result.error
    assert "sudo apt update" in result.error


@pytest.mark.asyncio
async def test_resolve_confirmation_deny_without_feedback():
    """当 resolve_confirmation 不传 feedback 时，为简单拒绝。"""
    from unittest.mock import AsyncMock, MagicMock

    llm = MagicMock(spec=LLMClient)
    llm.generate = AsyncMock()
    agent = Agent(
        llm=llm,
        system_prompt="test",
        workspace_dir="/tmp",
        max_steps=1,
    )

    req = ConfirmationRequired(command="rm -rf /tmp/test", reason="危险命令")
    agent.confirmation_request = req
    agent._confirmation_event.clear()

    async def resolve_after_delay():
        await asyncio.sleep(0.05)
        agent.resolve_confirmation(None)

    asyncio.create_task(resolve_after_delay())

    mock_tool = MagicMock()
    result = await agent._wait_for_confirmation(req, mock_tool, {})

    assert result.success is False
    assert "用户拒绝执行" in result.error
    assert "反馈" not in result.error  # 不应包含反馈相关文字
```

- [ ] **步骤 2：运行测试，确认失败**

执行：`uv run pytest tests/test_confirm_selector.py::test_resolve_confirmation_with_feedback -v`
预期：FAIL — `TypeError: resolve_confirmation() got an unexpected keyword argument 'feedback'`

- [ ] **步骤 3：在 Agent `__init__` 中添加 `_confirmation_feedback` 字段**

在 `mini_agent/agent.py` 第 94 行（`self._confirmation_result: str | None = None`）之后添加：

```python
        self._confirmation_feedback: str | None = None
```

- [ ] **步骤 4：更新 `resolve_confirmation` 方法，接受 feedback 参数**

替换 `mini_agent/agent.py` 第 577-584 行为：

```python
    def resolve_confirmation(self, result: str | None, feedback: str | None = None):
        """由 CLI 调用，在用户确认后恢复 Agent 执行。

        Args:
            result: "yes"、"always" 或 None（拒绝）
            feedback: 用户选择引导 LLM 时的自由文本反馈
        """
        self._confirmation_result = result
        self._confirmation_feedback = feedback
        self._confirmation_event.set()
```

- [ ] **步骤 5：更新 `_wait_for_confirmation` 方法，处理反馈**

替换 `mini_agent/agent.py` 第 534-575 行为：

```python
    async def _wait_for_confirmation(
        self, req: ConfirmationRequired, tool: Tool, arguments: dict
    ) -> ToolResult:
        """暂停执行，等待 CLI 处理确认请求。

        确认解决后返回工具执行结果。
        """
        self.confirmation_request = req
        self._confirmation_event.clear()
        print(f"\n{Colors.BRIGHT_YELLOW}⏸️  等待用户确认...{Colors.RESET}")

        # 等待 CLI 调用 resolve_confirmation()
        await self._confirmation_event.wait()

        request = self.confirmation_request
        result_text = self._confirmation_result
        feedback = self._confirmation_feedback
        self.confirmation_request = None
        self._confirmation_result = None
        self._confirmation_feedback = None

        if result_text is None:
            error_msg = f"用户拒绝执行: {request.command}"
            if feedback:
                error_msg = f"用户拒绝执行并给出反馈: {feedback}\n命令: {request.command}"
            return ToolResult(
                success=False,
                content="",
                error=error_msg,
            )

        # 用户确认 —— 应用规则并重新执行
        if hasattr(tool, "security") and tool.security:
            always = result_text == "always"
            tool.security.apply_confirmation(request.command, always=always)

        try:
            return await tool.execute(**arguments)
        except Exception as e:
            import traceback

            return ToolResult(
                success=False,
                content="",
                error=f"Tool execution failed: {type(e).__name__}: {e}\n\n{traceback.format_exc()}",
            )
```

- [ ] **步骤 6：运行测试，确认通过**

执行：`uv run pytest tests/test_confirm_selector.py -v`
预期：所有测试 PASS（包括 2 个新增的 Agent 反馈测试）

- [ ] **步骤 7：运行现有 Agent 测试，确认无回归**

执行：`uv run pytest tests/test_agent.py tests/test_bash_tool.py tests/test_security_confirm.py -v`
预期：所有现有测试 PASS

- [ ] **步骤 8：提交**

```bash
git add mini_agent/agent.py tests/test_confirm_selector.py
git commit -m "feat: add feedback support to agent confirmation flow"
```

---

### 任务 4：将 ConfirmSelector 集成到 CLI

**涉及文件：**
- 修改：`mini_agent/cli.py:838-856`（替换确认提示）

- [ ] **步骤 1：在 cli.py 顶部添加 import**

在 `mini_agent/cli.py` 的 import 区域（靠近其他 `from mini_agent` 导入处）添加：

```python
from mini_agent.ui.confirm_selector import ConfirmSelector
```

- [ ] **步骤 2：替换确认处理代码块**

将 `mini_agent/cli.py` 第 838-856 行（`if agent.confirmation_request is not None:` 内部的代码块）替换为：

```python
                        req = agent.confirmation_request
                        selector = ConfirmSelector(
                            command=req.command, reason=req.reason
                        )
                        result = await selector.select()

                        if result.action == "yes":
                            agent.resolve_confirmation("yes")
                        elif result.action == "always":
                            agent.resolve_confirmation("always")
                        elif result.action == "feedback":
                            agent.resolve_confirmation(None, feedback=result.feedback)
                        else:
                            agent.resolve_confirmation(None)
```

这替换了之前打印警告信息并使用 `session.prompt_async()` 的代码。

- [ ] **步骤 3：手动验证**

执行：`uv run python -m mini_agent.cli`

测试场景：
1. 触发需要确认的命令（例如让 agent 执行 `sudo echo test`）
2. 验证上下键可以在菜单中导航
3. 验证 `y`/`n`/`a`/`e` 快捷键直接选择有效
4. 验证 `e` 能打开反馈输入提示框
5. 验证反馈文字会出现在 agent 的下一轮对话中
6. 验证 `Ctrl+C` 和 `Escape` 等同于"拒绝"

- [ ] **步骤 4：提交**

```bash
git add mini_agent/cli.py
git commit -m "feat: replace text prompt with interactive ConfirmSelector in CLI"
```

---

### 任务 5：运行完整测试套件

- [ ] **步骤 1：运行所有测试**

执行：`uv run pytest tests/ -v`
预期：所有测试 PASS，无回归

- [ ] **步骤 2：修复可能的失败**

如果测试因 mock Agent 未初始化 `_confirmation_feedback` 字段而失败，在相关测试的 mock 设置中添加 `self._confirmation_feedback = None`。

- [ ] **步骤 3：如需修复则提交**

```bash
git add -A
git commit -m "test: fix test regressions from confirm selector integration"
```

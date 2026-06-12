"""交互式确认选择器，支持上下键导航。"""

from dataclasses import dataclass
from typing import Literal

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.styles import Style as PromptStyle


@dataclass
class ConfirmResult:
    """用户确认交互的结果。

    Attributes:
        action: 用户的选择 —— "yes"、"no"、"always" 或 "feedback"。
        feedback: 当 action 为 "feedback" 时的自由文本反馈，其他情况为 None。
    """

    action: Literal["yes", "no", "always", "feedback"]
    feedback: str | None = None


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

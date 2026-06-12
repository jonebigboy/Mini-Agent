"""Tests for the confirm selector component."""

import asyncio

import pytest

from mini_agent.ui.confirm_selector import ConfirmResult, ConfirmSelector


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


@pytest.mark.asyncio
async def test_resolve_confirmation_with_feedback():
    """当 resolve_confirmation 传入 feedback 时，拒绝的 ToolResult
    应包含反馈文字。"""
    from unittest.mock import AsyncMock, MagicMock

    from mini_agent.agent import Agent
    from mini_agent.llm.llm_wrapper import LLMClient
    from mini_agent.tools.base import ConfirmationRequired, ToolResult

    llm = MagicMock(spec=LLMClient)
    llm.generate = AsyncMock()
    agent = Agent(
        llm_client=llm,
        tools=[],
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

    from mini_agent.agent import Agent
    from mini_agent.llm.llm_wrapper import LLMClient
    from mini_agent.tools.base import ConfirmationRequired, ToolResult

    llm = MagicMock(spec=LLMClient)
    llm.generate = AsyncMock()
    agent = Agent(
        llm_client=llm,
        tools=[],
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

"""Verify Agent uses SessionWriter instead of AgentLogger."""
from unittest.mock import MagicMock

import pytest

from mini_agent.agent import Agent
from mini_agent.schema import Message
from mini_agent.session.writer import SessionWriter


@pytest.fixture
def agent_with_writer(mini_agent_home, tmp_path):
    from mini_agent.llm import LLMClient

    writer = SessionWriter(
        session_id="agent-sid",
        cwd=str(tmp_path),
        workspace=str(tmp_path),
        model="test",
        cli_args={},
    )
    writer.open()
    llm = MagicMock(spec=LLMClient)
    agent = Agent(
        llm_client=llm,
        system_prompt="test",
        tools=[],
        workspace_dir=str(tmp_path),
        session_writer=writer,
    )
    yield agent, writer
    writer.close()


def test_agent_has_session_not_logger(agent_with_writer):
    agent, writer = agent_with_writer
    assert agent.session is writer
    assert not hasattr(agent, "logger") or agent.logger is None


def test_agent_add_user_message_persists(agent_with_writer):
    agent, writer = agent_with_writer
    agent.add_user_message("test input")
    # The session_writer should have recorded a user message event
    # session_start is seq 0, this user message is seq 1
    assert writer._seq >= 1  # session_start (0) + this message (1)


def test_agent_tracks_message_seqs(agent_with_writer):
    """Agent should maintain _message_seqs parallel to messages for summary correlation."""
    agent, writer = agent_with_writer
    agent.add_user_message("hello")
    # session_start (seq 0) is NOT in _message_seqs (it's a control event, not a message)
    # user message is seq 1
    assert agent._message_seqs == [1]

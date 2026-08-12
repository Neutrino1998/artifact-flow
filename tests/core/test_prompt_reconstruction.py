from unittest.mock import AsyncMock

import pytest

from core.conversation_manager import ConversationManager
from core.events import ExecutionEvent, StreamEventType


@pytest.mark.asyncio
async def test_reconstruct_model_messages_keeps_native_call_ids():
    native_calls = [{
        "id": "call_search",
        "type": "function",
        "function": {"name": "search", "arguments": '{"q":"artifact"}'},
    }]
    events = [
        ExecutionEvent(
            event_type=StreamEventType.USER_INPUT.value,
            agent_name="lead_agent",
            data={"content": "find it"},
            event_id="evt-user",
        ),
        ExecutionEvent(
            event_type=StreamEventType.LLM_COMPLETE.value,
            agent_name="lead_agent",
            data={"content": "", "tool_calls": native_calls},
            event_id="evt-llm",
        ),
        ExecutionEvent(
            event_type=StreamEventType.TOOL_COMPLETE.value,
            agent_name="lead_agent",
            data={
                "call_id": "call_search",
                "tool": "search",
                "success": True,
                "result_data": "found",
            },
            event_id="evt-tool",
        ),
        ExecutionEvent(
            event_type=StreamEventType.AGENT_START.value,
            agent_name="lead_agent",
            data={
                "system_prompt": "system",
                "reminder": "current context",
                "model": "openai/test-model",
                "exposed_tool_names": ["search"],
            },
            event_id="evt-anchor",
        ),
    ]
    manager = ConversationManager(AsyncMock())
    manager.load_event_history_async = AsyncMock(return_value=events)

    result = await manager.reconstruct_prompt("conv-1", "msg-1", "evt-anchor")

    assert result is not None
    assert result["model"] == "openai/test-model"
    assert result["exposed_tool_names"] == ["search"]
    assert result["has_reminder"] is True
    assert "tools" not in result
    assert [message["role"] for message in result["messages"]] == [
        "system", "user", "assistant", "tool", "user",
    ]
    assert result["messages"][2]["tool_calls"] == native_calls
    assert result["messages"][3]["tool_call_id"] == "call_search"


@pytest.mark.asyncio
async def test_reconstruct_prompt_uses_persisted_reasoning_replay_policy():
    events = [
        ExecutionEvent(
            event_type=StreamEventType.USER_INPUT.value,
            agent_name="lead_agent",
            data={"content": "question"},
            event_id="evt-user",
        ),
        ExecutionEvent(
            event_type=StreamEventType.LLM_COMPLETE.value,
            agent_name="lead_agent",
            data={"content": "answer", "reasoning_content": "private reasoning"},
            event_id="evt-llm",
        ),
        ExecutionEvent(
            event_type=StreamEventType.USER_INPUT.value,
            agent_name="lead_agent",
            data={"content": "follow-up"},
            event_id="evt-follow-up",
        ),
        ExecutionEvent(
            event_type=StreamEventType.AGENT_START.value,
            agent_name="lead_agent",
            data={
                "system_prompt": "system",
                "reminder": "current context",
                "model": "model-alias",
                "replay_reasoning": False,
            },
            event_id="evt-anchor",
        ),
    ]
    manager = ConversationManager(AsyncMock())
    manager.load_event_history_async = AsyncMock(return_value=events)

    result = await manager.reconstruct_prompt("conv-1", "msg-1", "evt-anchor")

    assert result is not None
    assert result["exposed_tool_names"] is None
    assistant = next(m for m in result["messages"] if m["role"] == "assistant")
    assert assistant["content"] == "answer"
    assert "reasoning_content" not in assistant

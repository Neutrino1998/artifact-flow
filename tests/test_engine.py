"""
Engine execute_loop unit tests.

These test the engine in isolation (no DB, no LLM) by providing
minimal state and mock agents/tools.
"""

import pytest
from dataclasses import dataclass, field
from unittest.mock import patch, AsyncMock

from core.engine import create_initial_state, execute_loop
from core.events import StreamEventType
from api.services.task_manager import TaskManager


# ============================================================
# Shared helpers
# ============================================================


@dataclass
class _FakeAgentConfig:
    """Minimal AgentConfig stub."""
    name: str = "lead_agent"
    description: str = "test"
    capabilities: list = field(default_factory=list)
    tools: dict = field(default_factory=dict)
    model: str = "fake-model"
    max_tool_rounds: int = 3
    role_prompt: str = ""


def _make_fake_stream(chunks: list[dict]):
    """Create a fake async generator that yields pre-configured chunks."""
    async def fake(messages, **kwargs):
        for chunk in chunks:
            yield chunk
    return fake


async def _run_with_fake_llm(chunks: list[dict], agent_config=None):
    """Helper: run execute_loop with a fake LLM returning given chunks."""
    state = create_initial_state(
        task="hello",
        session_id="sess-1",
        message_id="msg-1",
        conversation_history=[],
    )

    if agent_config is None:
        agent_config = _FakeAgentConfig()

    task_manager = TaskManager(max_concurrent=1)
    emitted: list = []

    async def capture_emit(event_dict):
        emitted.append(event_dict)

    with patch("models.llm.astream_with_retry", _make_fake_stream(chunks)):
        result = await execute_loop(
            state=state,
            agents={"lead_agent": agent_config},
            tools={},
            task_manager=task_manager,
            emit=capture_emit,
        )

    await task_manager.shutdown()
    return result, emitted


# ============================================================
# Tests
# ============================================================


class TestAgentNotFound:
    """Engine should produce exactly one error event when agent is missing."""

    async def test_agent_not_found_sets_error_flag(self):
        state = create_initial_state(
            task="hello",
            session_id="sess-1",
            message_id="msg-1",
            conversation_history=[],
        )
        # Override to a non-existent agent
        state["current_agent"] = "nonexistent_agent"

        task_manager = TaskManager(max_concurrent=1)
        emitted: list = []

        async def capture_emit(event_dict):
            emitted.append(event_dict)

        result = await execute_loop(
            state=state,
            agents={},  # no agents registered
            tools={},
            task_manager=task_manager,
            emit=capture_emit,
        )

        # State must be marked as error
        assert result["error"] is True
        assert "nonexistent_agent" in result["response"]

        # Exactly one error event in state["events"] (no duplicate)
        error_events = [
            e for e in result["events"]
            if e.event_type == StreamEventType.ERROR.value
        ]
        assert len(error_events) == 1

        # Exactly one error event emitted via SSE
        sse_errors = [e for e in emitted if e["type"] == StreamEventType.ERROR.value]
        assert len(sse_errors) == 1

        # No complete event should be emitted
        sse_completes = [e for e in emitted if e["type"] == StreamEventType.COMPLETE.value]
        assert len(sse_completes) == 0

        await task_manager.shutdown()


class TestLlmChunkAccumulation:
    """llm_chunk events must carry cumulative content, not incremental deltas."""

    async def test_content_chunks_are_cumulative(self):
        """Each llm_chunk.content should contain all text received so far."""
        chunks = [
            {"type": "content", "content": "让我"},
            {"type": "content", "content": "来分析"},
            {"type": "content", "content": "这个问题"},
            {"type": "usage", "token_usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}},
            {"type": "final", "content": "让我来分析这个问题", "reasoning_content": None, "token_usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}},
        ]

        result, emitted = await _run_with_fake_llm(chunks)

        llm_chunks = [
            e for e in emitted
            if e["type"] == StreamEventType.LLM_CHUNK.value and "content" in (e.get("data") or {})
        ]

        assert len(llm_chunks) == 3
        assert llm_chunks[0]["data"]["content"] == "让我"
        assert llm_chunks[1]["data"]["content"] == "让我来分析"
        assert llm_chunks[2]["data"]["content"] == "让我来分析这个问题"

        # Final state
        assert result["completed"] is True
        assert result["response"] == "让我来分析这个问题"

    async def test_reasoning_chunks_are_cumulative(self):
        """Each llm_chunk.reasoning_content should contain all reasoning so far."""
        chunks = [
            {"type": "reasoning", "content": "首先"},
            {"type": "reasoning", "content": "分析"},
            {"type": "reasoning", "content": "需求"},
            {"type": "content", "content": "结果如下"},
            {"type": "usage", "token_usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
            {"type": "final", "content": "结果如下", "reasoning_content": "首先分析需求", "token_usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
        ]

        result, emitted = await _run_with_fake_llm(chunks)

        reasoning_chunks = [
            e for e in emitted
            if e["type"] == StreamEventType.LLM_CHUNK.value and "reasoning_content" in (e.get("data") or {})
        ]

        assert len(reasoning_chunks) == 3
        assert reasoning_chunks[0]["data"]["reasoning_content"] == "首先"
        assert reasoning_chunks[1]["data"]["reasoning_content"] == "首先分析"
        assert reasoning_chunks[2]["data"]["reasoning_content"] == "首先分析需求"

        # Content chunks should also be present and cumulative
        content_chunks = [
            e for e in emitted
            if e["type"] == StreamEventType.LLM_CHUNK.value and "content" in (e.get("data") or {})
        ]
        assert len(content_chunks) == 1
        assert content_chunks[0]["data"]["content"] == "结果如下"

    async def test_llm_complete_contains_full_content(self):
        """llm_complete event should contain the complete accumulated content."""
        chunks = [
            {"type": "reasoning", "content": "思考中"},
            {"type": "content", "content": "答案"},
            {"type": "usage", "token_usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
            {"type": "final", "content": "答案", "reasoning_content": "思考中", "token_usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
        ]

        result, emitted = await _run_with_fake_llm(chunks)

        llm_completes = [
            e for e in emitted
            if e["type"] == StreamEventType.LLM_COMPLETE.value
        ]

        assert len(llm_completes) == 1
        assert llm_completes[0]["data"]["content"] == "答案"
        assert llm_completes[0]["data"]["reasoning_content"] == "思考中"

    async def test_llm_chunks_are_sse_only(self):
        """llm_chunk events should NOT appear in state['events'] (sse_only=True)."""
        chunks = [
            {"type": "content", "content": "hello"},
            {"type": "usage", "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            {"type": "final", "content": "hello", "reasoning_content": None, "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        ]

        result, emitted = await _run_with_fake_llm(chunks)

        # llm_chunk should be emitted via SSE
        sse_chunks = [e for e in emitted if e["type"] == StreamEventType.LLM_CHUNK.value]
        assert len(sse_chunks) >= 1

        # But NOT in state["events"]
        state_chunks = [
            e for e in result["events"]
            if e.event_type == StreamEventType.LLM_CHUNK.value
        ]
        assert len(state_chunks) == 0

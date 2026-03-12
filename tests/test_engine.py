"""
Engine execute_loop unit tests.

These test the engine in isolation (no DB, no LLM) by providing
minimal state and mock agents/tools.
"""

import pytest

from core.state import create_initial_state
from core.engine import execute_loop
from core.events import StreamEventType
from api.services.task_manager import TaskManager


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
            tool_registry=_FakeRegistry(),
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


class _FakeRegistry:
    """Minimal stub for ToolRegistry."""
    def get_tool(self, name: str):
        return None

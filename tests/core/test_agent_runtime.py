"""AgentRuntime outcome and dependency-boundary contracts."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from core.agent_runtime import (
    AgentInvocation,
    AgentRuntime,
    StopReason,
)
from core.agent_runtime import RuntimeHooks
from core.engine import create_initial_state
from core.events import ExecutionEvent, StreamEventType


def _hooks() -> RuntimeHooks:
    async def false(_message_id: str) -> bool:
        return False

    async def no_interrupt(_message_id, _payload, _timeout):
        return None

    async def no_messages(_message_id):
        return []

    return RuntimeHooks(false, no_interrupt, no_messages)


def _state() -> dict:
    return create_initial_state("hello", "session", "message")


def _runtime(*, timeout: float = 10) -> AgentRuntime:
    return AgentRuntime(
        agents={},
        tools={},
        effective_toolsets={},
        timeout=timeout,
    )


async def test_complete_outcome_and_entry_agent_are_forwarded():
    state = _state()
    seen = {}

    async def complete(**kwargs):
        seen.update(kwargs)
        kwargs["state"]["completed"] = True
        kwargs["state"]["response"] = "done"
        return kwargs["state"]

    with patch("core.agent_runtime.execute_loop", side_effect=complete):
        outcome = await _runtime().run(
            AgentInvocation(state=state, entry_agent="research_agent"),
            hooks=_hooks(),
        )

    assert outcome.stop_reason == StopReason.COMPLETE
    assert outcome.state is state
    assert seen["entry_agent"] == "research_agent"


async def test_cooperative_cancel_is_distinct_from_external_cancel():
    state = _state()

    async def cooperative(**kwargs):
        kwargs["state"]["cancelled"] = True
        kwargs["state"]["completed"] = True
        return kwargs["state"]

    with patch("core.agent_runtime.execute_loop", side_effect=cooperative):
        outcome = await _runtime().run(
            AgentInvocation(state=state), hooks=_hooks()
        )

    assert outcome.stop_reason == StopReason.COOPERATIVE_CANCEL


async def test_timeout_returns_state_without_emitting_terminal():
    state = _state()

    async def blocked(**_kwargs):
        await asyncio.sleep(60)

    with patch("core.agent_runtime.execute_loop", side_effect=blocked):
        outcome = await _runtime(timeout=0.01).run(
            AgentInvocation(state=state), hooks=_hooks()
        )

    assert outcome.stop_reason == StopReason.TIMEOUT
    assert state["timed_out"] is True
    assert not any(
        event.event_type in {
            StreamEventType.COMPLETE.value,
            StreamEventType.TIMED_OUT.value,
            StreamEventType.CANCELLED.value,
            StreamEventType.ERROR.value,
        }
        for event in state["events"]
    )


async def test_task_cancel_returns_external_cancel_outcome_with_partial_state():
    state = _state()
    started = asyncio.Event()

    async def blocked(**kwargs):
        kwargs["state"]["events"].append(ExecutionEvent(
            event_type=StreamEventType.LLM_COMPLETE.value,
            agent_name="lead_agent",
            data={"content": "partial"},
        ))
        started.set()
        await asyncio.sleep(60)

    with patch("core.agent_runtime.execute_loop", side_effect=blocked):
        task = asyncio.create_task(
            _runtime().run(AgentInvocation(state=state), hooks=_hooks())
        )
        await started.wait()
        task.cancel()
        outcome = await task

    assert outcome.stop_reason == StopReason.EXTERNAL_CANCEL
    assert outcome.state is state
    assert state["cancelled"] is True
    assert [event.event_type for event in state["events"]] == [
        StreamEventType.LLM_COMPLETE.value
    ]


async def test_unexpected_runtime_error_is_recorded_not_emitted():
    state = _state()

    async def fail(**_kwargs):
        raise RuntimeError("boom")

    with patch("core.agent_runtime.execute_loop", side_effect=fail):
        outcome = await _runtime().run(
            AgentInvocation(state=state), hooks=_hooks()
        )

    assert outcome.stop_reason == StopReason.ERROR
    assert state["error"] is True
    assert state["error_detail"]["error"] == "boom"
    assert state["events"] == []


def test_import_does_not_load_web_database_or_redis_modules():
    project_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": str(project_root / "src"),
    }
    code = """
import sys
import core.agent_runtime
forbidden = ('fastapi', 'sqlalchemy', 'redis', 'api.services', 'repositories')
loaded = sorted(name for name in sys.modules if name.startswith(forbidden))
assert not loaded, loaded
"""
    subprocess.run([sys.executable, "-c", code], env=env, check=True)

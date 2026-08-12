"""AgentRuntime outcome and dependency-boundary contracts."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from core.execution.agent_runtime import (
    AgentInvocation,
    AgentRuntime,
    StopReason,
    get_stop_reason,
    stop_execution,
)
from core.execution.agent_runtime import RuntimeHooks
from core.execution.engine import create_initial_state
from core.execution.events import ExecutionEvent, StreamEventType


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


def test_stop_transition_is_first_terminal_wins_unless_boundary_replaces_it():
    state = {"stop_reason": None}

    assert stop_execution(state, StopReason.ERROR) is StopReason.ERROR
    assert stop_execution(state, StopReason.COMPLETE) is StopReason.ERROR
    assert get_stop_reason(state) is StopReason.ERROR

    assert (
        stop_execution(state, StopReason.TIMEOUT, replace=True)
        is StopReason.TIMEOUT
    )
    assert get_stop_reason(state) is StopReason.TIMEOUT


async def test_complete_outcome_and_entry_agent_are_forwarded():
    state = _state()
    seen = {}

    async def complete(**kwargs):
        seen.update(kwargs)
        kwargs["state"]["stop_reason"] = StopReason.COMPLETE
        kwargs["state"]["response"] = "done"
        return kwargs["state"]

    with patch("core.execution.agent_runtime.execute_loop", side_effect=complete):
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
        kwargs["state"]["stop_reason"] = StopReason.COOPERATIVE_CANCEL
        return kwargs["state"]

    with patch("core.execution.agent_runtime.execute_loop", side_effect=cooperative):
        outcome = await _runtime().run(
            AgentInvocation(state=state), hooks=_hooks()
        )

    assert outcome.stop_reason == StopReason.COOPERATIVE_CANCEL


async def test_timeout_returns_state_without_emitting_terminal():
    state = _state()

    async def blocked(**_kwargs):
        await asyncio.sleep(60)

    with patch("core.execution.agent_runtime.execute_loop", side_effect=blocked):
        outcome = await _runtime(timeout=0.01).run(
            AgentInvocation(state=state), hooks=_hooks()
        )

    assert outcome.stop_reason == StopReason.TIMEOUT
    assert state["stop_reason"] is StopReason.TIMEOUT
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

    with patch("core.execution.agent_runtime.execute_loop", side_effect=blocked):
        task = asyncio.create_task(
            _runtime().run(AgentInvocation(state=state), hooks=_hooks())
        )
        await started.wait()
        task.cancel()
        outcome = await task

    assert outcome.stop_reason == StopReason.EXTERNAL_CANCEL
    assert outcome.state is state
    assert state["stop_reason"] is StopReason.EXTERNAL_CANCEL
    assert [event.event_type for event in state["events"]] == [
        StreamEventType.LLM_COMPLETE.value
    ]


async def test_unexpected_runtime_error_is_recorded_not_emitted():
    state = _state()

    async def fail(**_kwargs):
        raise RuntimeError("boom")

    with patch("core.execution.agent_runtime.execute_loop", side_effect=fail):
        outcome = await _runtime().run(
            AgentInvocation(state=state), hooks=_hooks()
        )

    assert outcome.stop_reason == StopReason.ERROR
    assert state["stop_reason"] is StopReason.ERROR
    assert state["response"] == "Execution failed unexpectedly. Please retry."
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
import core.execution.agent_runtime
forbidden = ('fastapi', 'sqlalchemy', 'redis', 'api.services', 'repositories')
loaded = sorted(name for name in sys.modules if name.startswith(forbidden))
assert not loaded, loaded
"""
    subprocess.run([sys.executable, "-c", code], env=env, check=True)


def test_embedded_runtime_runs_shipped_config_without_server_initialization():
    """The internal embedded boundary executes with real config and a fake model.

    Keep this in a fresh interpreter: besides proving the direct runtime call, it
    catches accidental imports of the Web assembly path that the wider pytest
    process may already have loaded through unrelated fixtures.
    """
    project_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": str(project_root / "src"),
    }
    code = r'''
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

from core.execution.agent_runtime import AgentInvocation, AgentRuntime, RuntimeHooks, StopReason
from core.capabilities.effective_toolset import resolve_all
from core.execution.engine import create_initial_state
from core.execution.events import StreamEventType
from models.llm import validate_agent_model_config
from reconcile.seeds import parse_agent_seeds, parse_tool_seeds
from reconcile.snapshot import AgentSnapshot, RegistrySnapshot, UnitInfo, build_http_tool
from tools.base import build_tool_map
from tools.builtin.call_subagent import CallSubagentTool
from tools.builtin.search_tools import SearchToolsTool
from tools.builtin.web_fetch import WebFetchTool
from tools.builtin.web_search import WebSearchTool


async def main():
    root = Path.cwd()
    tool_seeds = parse_tool_seeds(str(root / "config" / "tools"))
    known_full_names = {
        member.full_name: seed.name
        for seed in tool_seeds
        for member in seed.members
    }
    agent_seeds = parse_agent_seeds(
        str(root / "config" / "agents"),
        known_unit_names={seed.name for seed in tool_seeds},
        known_full_names=known_full_names,
    )

    units = {}
    external_tools = {}
    for seed in tool_seeds:
        unit = UnitInfo(
            name=seed.name,
            kind=seed.kind,
            description=seed.description,
            visibility=seed.visibility,
            defer=seed.defer,
            provider=seed.provider,
            source="seeded",
            provider_config=seed.provider_config,
        )
        for member in seed.members:
            unit.member_full_names.append(member.full_name)
            external_tools[member.full_name] = build_http_tool(
                member.full_name,
                member.permission,
                member.definition,
                unit_name=seed.name,
            )
        units[seed.name] = unit

    agents = {
        seed.name: AgentSnapshot(
            name=seed.name,
            description=seed.description,
            model=seed.model,
            internal=seed.internal,
            role_prompt=seed.role_prompt,
            builtin_tools=dict(seed.builtin_tools),
            units={unit.unit_name: unit.member_state for unit in seed.units},
        )
        for seed in agent_seeds
    }
    validate_agent_model_config({name: agent.model for name, agent in agents.items()})
    assert "lead_agent" in agents

    public_subagents = [
        name for name, agent in agents.items()
        if name != "lead_agent" and not agent.internal
    ]
    tools = build_tool_map(
        [
            CallSubagentTool(valid_agents=public_subagents),
            WebSearchTool(),
            WebFetchTool(),
            SearchToolsTool(),
        ],
        list(external_tools.values()),
    )
    snapshot = RegistrySnapshot(
        external_tools=external_tools,
        units=units,
        agents=agents,
    )
    effective_toolsets = resolve_all(snapshot, tools)

    async def not_cancelled(_message_id):
        return False

    async def no_interrupt(_message_id, _payload, _timeout):
        return None

    async def no_messages(_message_id):
        return []

    async def fake_llm(_messages, **_kwargs):
        usage = {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        }
        yield {"type": "content", "content": "embedded ok"}
        yield {"type": "usage", "token_usage": usage}
        yield {
            "type": "final",
            "content": "embedded ok",
            "reasoning_content": None,
            "token_usage": usage,
        }

    live_events = []

    async def collect(event):
        live_events.append(event)

    runtime = AgentRuntime(
        agents=agents,
        tools=tools,
        effective_toolsets=effective_toolsets,
        timeout=10,
    )
    hooks = RuntimeHooks(not_cancelled, no_interrupt, no_messages)
    state = create_initial_state("Say hello", "embedded-session", "embedded-message")

    with patch("models.llm.astream_with_retry", fake_llm):
        outcome = await runtime.run(
            AgentInvocation(state=state, entry_agent="lead_agent"),
            hooks=hooks,
            event_sink=collect,
        )

    assert outcome.stop_reason is StopReason.COMPLETE
    assert outcome.state["response"] == "embedded ok"
    persisted_types = [event.event_type for event in outcome.state["events"]]
    assert persisted_types == [
        StreamEventType.USER_INPUT.value,
        StreamEventType.AGENT_START.value,
        StreamEventType.LLM_COMPLETE.value,
        StreamEventType.AGENT_COMPLETE.value,
    ]
    assert any(event["type"] == StreamEventType.LLM_CHUNK.value for event in live_events)
    assert not any(
        event_type in {
            StreamEventType.COMPLETE.value,
            StreamEventType.CANCELLED.value,
            StreamEventType.TIMED_OUT.value,
            StreamEventType.ERROR.value,
        }
        for event_type in persisted_types
    )

    forbidden = (
        "api.main",
        "api.dependencies",
        "api.services",
        "api.routers",
        "redis.asyncio",
    )
    loaded = sorted(name for name in sys.modules if name.startswith(forbidden))
    assert not loaded, loaded


asyncio.run(main())
'''
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        env=env,
        check=True,
    )

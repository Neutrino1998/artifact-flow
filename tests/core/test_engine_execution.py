"""
Engine execution flow tests.

Covers: agent routing, tool execution, cancellation, permission interrupts,
subagent routing, round limits, pending message drain, and metrics.

Mock strategy: patch("models.llm.astream_with_retry") + real RuntimeStore.
"""

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from core.engine import EngineHooks, create_initial_state, execute_loop
from core.events import StreamEventType, ExecutionEvent
from tests.core._toolset import effective_for
from api.services.runtime_store import InMemoryRuntimeStore
from tools.base import ArtifactSpec, BaseTool, ToolPermission, ToolResult


# ============================================================
# Helpers
# ============================================================

_CALL_IDS = itertools.count()


@dataclass
class _FakeAgentConfig:
    name: str = "lead_agent"
    description: str = "test lead"
    tools: dict = field(default_factory=dict)
    model: str = "openai/fake-model"
    max_tool_rounds: int = 3
    role_prompt: str = "You are a test agent."
    internal: bool = False


def _make_fake_stream(chunks: list[dict]):
    """Single-round fake async LLM generator."""
    async def fake(messages, **kwargs):
        for c in chunks:
            yield c
    return fake


def _make_fake_stream_sequence(rounds: list[list[dict]]):
    """Multi-round: each call to the LLM pops the next round's chunks."""
    call_count = {"n": 0}

    async def fake(messages, **kwargs):
        idx = min(call_count["n"], len(rounds) - 1)
        call_count["n"] += 1
        for c in rounds[idx]:
            yield c

    return fake


def _make_cancelling_stream(chunks: list[dict], store, message_id: str, cancel_before_idx: int = 1):
    """Fake stream that sets the store's cancel flag right before yielding
    chunks[cancel_before_idx], so the engine's in-stream cancel poll trips on
    that chunk (use with cancel_check_interval=0)."""
    async def fake(messages, **kwargs):
        for i, c in enumerate(chunks):
            if i == cancel_before_idx:
                store._cancellations[message_id] = asyncio.Event()
                store._cancellations[message_id].set()
            yield c
    return fake


def _simple_llm_chunks(text: str, input_tokens: int = 10, output_tokens: int = 5):
    """Build standard LLM chunks for a simple text response."""
    return [
        {"type": "content", "content": text},
        {"type": "usage", "token_usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }},
        {"type": "final", "content": text, "reasoning_content": None, "token_usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }},
    ]


def _native_tool_call(tool_name: str, **params) -> list[dict]:
    """Build one normalized native tool call."""
    return [{
        "id": f"call_{tool_name}_{next(_CALL_IDS)}",
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(params),
        },
    }]


def _tool_call_chunks(tool_calls, input_tokens: int = 10, output_tokens: int = 5):
    """Build adapter chunks for an accepted native tool-call envelope."""
    if isinstance(tool_calls, str):
        return _simple_llm_chunks(tool_calls, input_tokens, output_tokens)
    usage = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    return [
        {"type": "usage", "token_usage": usage},
        {
            "type": "final",
            "content": "",
            "reasoning_content": None,
            "tool_calls": tool_calls,
            "token_usage": usage,
        },
    ]


class _FakeTool(BaseTool):
    """Configurable fake tool for testing."""

    def __init__(self, name: str, result: ToolResult = None, permission: ToolPermission = ToolPermission.AUTO):
        super().__init__(name=name, description=f"Fake {name}", permission=permission)
        self._result = result or ToolResult(success=True, data="ok")

    def get_input_schema(self):
        return {"type": "object", "properties": {}, "additionalProperties": True}

    async def execute(self, **params) -> ToolResult:
        return self._result

    async def __call__(self, **params) -> ToolResult:
        return await self.execute(**params)


class _FailingTool(BaseTool):
    """Tool that raises an exception."""

    def __init__(self, name: str):
        super().__init__(name=name, description=f"Failing {name}", permission=ToolPermission.AUTO)

    def get_input_schema(self):
        return {"type": "object", "properties": {}, "additionalProperties": True}

    async def execute(self, **params) -> ToolResult:
        raise RuntimeError("tool exploded")

    async def __call__(self, **params) -> ToolResult:
        return await self.execute(**params)


class _RecordingArtifactService:
    """Small engine collaborator that records tool-result artifact content."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def set_session(self, session_id: str) -> None:
        pass

    async def list_artifacts(self, session_id: str, **kwargs):
        return []

    async def ingest_tool_result(
        self, session_id: str, spec: ArtifactSpec, tool_name: str = None
    ):
        self.calls.append((session_id, tool_name, spec.content))
        return True, "ok", f"tool_{tool_name}_0001"


def _hooks_from_store(store: InMemoryRuntimeStore) -> EngineHooks:
    """Build EngineHooks wired to a real RuntimeStore."""
    return EngineHooks(
        check_cancelled=store.is_cancelled,
        wait_for_interrupt=store.wait_for_interrupt,
        drain_messages=store.drain_messages,
    )


async def _run_engine(
    llm_factory,
    agents=None,
    tools=None,
    task="hello",
    message_id="msg-1",
    path_events=None,
    store=None,
    permission_timeout=1,
    cancel_check_interval=None,
    force_compact=False,
    artifact_service=None,
    effective_toolsets=None,
    agent_progressive_state=None,
):
    """Helper to run engine with given LLM factory and return (state, emitted).

    cancel_check_interval: override config.CANCEL_CHECK_INTERVAL — pass 0 to make
    the in-stream cancel poll fire on every chunk (fake streams are instant, so
    the real 0.5s throttle would otherwise never trip in a test).
    """
    state = create_initial_state(
        task=task,
        session_id="sess-1",
        message_id=message_id,
        path_events=path_events or [],
        force_compact=force_compact,
        agent_progressive_state=agent_progressive_state,
    )

    if store is None:
        store = InMemoryRuntimeStore()

    emitted = []

    async def capture_emit(event_dict):
        emitted.append(event_dict)

    if agents is None:
        agents = {"lead_agent": _FakeAgentConfig()}

    with patch("models.llm.astream_with_retry", llm_factory), \
         patch("core.engine.config") as mock_config:
        # Copy real config values then override permission_timeout for fast tests
        from config import config as real_config
        for attr in dir(real_config):
            if attr.isupper():
                setattr(mock_config, attr, getattr(real_config, attr))
        mock_config.PERMISSION_TIMEOUT = permission_timeout
        if cancel_check_interval is not None:
            mock_config.CANCEL_CHECK_INTERVAL = cancel_check_interval
        result = await execute_loop(
            state=state,
            agents=agents,
            tools=tools or {},
            effective_toolsets=(
                effective_toolsets or effective_for(agents, tools or {})
            ),
            hooks=_hooks_from_store(store),
            artifact_service=artifact_service,
            emit=capture_emit,
        )

    return result, emitted, store


def _events_of_type(emitted, event_type):
    return [e for e in emitted if e["type"] == event_type]


# ============================================================
# TestLeadCompletion
# ============================================================


class TestLeadCompletion:

    async def test_plain_text_completes(self):
        result, emitted, store = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("Done!"))
        )
        assert result["completed"] is True
        assert result["response"] == "Done!"

    async def test_agent_start_and_complete_events(self):
        result, emitted, store = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("ok"))
        )

        starts = _events_of_type(emitted, "agent_start")
        completes = _events_of_type(emitted, "agent_complete")
        assert len(starts) == 1
        assert len(completes) == 1
        assert starts[0]["agent"] == "lead_agent"
        assert completes[0]["agent"] == "lead_agent"
        assert starts[0]["data"]["model"] == "openai/fake-model"
        assert "tools" not in starts[0]["data"]

    async def test_agent_start_does_not_persist_native_tool_schemas(self):
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        tool = _FakeTool("my_tool")

        _, emitted, _ = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("ok")),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        start = _events_of_type(emitted, "agent_start")[0]
        assert start["data"]["model"] == agent.model
        assert "tools" not in start["data"]


# ============================================================
# TestSubagentRouting
# ============================================================


class TestSubagentRouting:

    async def test_subagent_no_tools_returns_to_lead(self):
        """Subagent with plain text → switch back to lead, response packed as tool_result."""
        sub_config = _FakeAgentConfig(name="search_agent", tools={})
        lead_config = _FakeAgentConfig(
            tools={"call_subagent": "auto"},
        )

        call_subagent_xml = _native_tool_call(
            "call_subagent",
            agent_name="search_agent",
            instruction="find stuff",
        )

        class CallSubagentTool(BaseTool):
            def __init__(self):
                super().__init__(name="call_subagent", description="Dispatch", permission=ToolPermission.AUTO)
            def get_input_schema(self):
                return {"type": "object", "properties": {}, "additionalProperties": True}
            async def execute(self, **p): return ToolResult(success=True, data="ok")
            async def __call__(self, **p): return await self.execute(**p)

        rounds = [
            _tool_call_chunks(call_subagent_xml),       # lead calls subagent
            _simple_llm_chunks("search result here"),     # subagent responds
            _simple_llm_chunks("Final answer"),           # lead completes
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": lead_config, "search_agent": sub_config},
            tools={"call_subagent": CallSubagentTool()},
        )

        assert result["completed"] is True
        assert result["response"] == "Final answer"

        # Should have subagent_instruction event in state events
        sub_instructions = [
            e for e in result["events"]
            if e.event_type == StreamEventType.SUBAGENT_INSTRUCTION.value
        ]
        assert len(sub_instructions) == 1
        assert sub_instructions[0].agent_name == "search_agent"

        # tool_complete for call_subagent should contain subagent response
        tool_completes = _events_of_type(emitted, "tool_complete")
        subagent_results = [tc for tc in tool_completes if tc["data"].get("tool") == "call_subagent"]
        assert any("search result here" in str(tc["data"].get("result_data", "")) for tc in subagent_results)

    async def test_large_subagent_result_uses_tool_result_artifact_path(self):
        """The special recursive route still obeys the common per-call inline limit."""
        from config import config
        from tools.builtin.call_subagent import CallSubagentTool

        lead = _FakeAgentConfig(tools={"call_subagent": "auto"})
        sub = _FakeAgentConfig(name="sub_agent", tools={})
        long_response = "S" * (config.TOOL_RESULT_INLINE_MAX_CHARS + 1)
        xml = _native_tool_call(
            "call_subagent", agent_name="sub_agent", instruction="return the result"
        )
        artifact_service = _RecordingArtifactService()

        result, emitted, _ = await _run_engine(
            _make_fake_stream_sequence([
                _tool_call_chunks(xml),
                _simple_llm_chunks(long_response),
                _simple_llm_chunks("lead done"),
            ]),
            agents={"lead_agent": lead, "sub_agent": sub},
            tools={"call_subagent": CallSubagentTool(valid_agents=["sub_agent"])},
            artifact_service=artifact_service,
        )

        assert result["response"] == "lead done"
        assert len(artifact_service.calls) == 1
        _, tool_name, stored = artifact_service.calls[0]
        assert tool_name == "call_subagent"
        assert long_response in stored

        complete = next(
            e for e in emitted
            if e["type"] == "tool_complete"
            and e["data"].get("tool") == "call_subagent"
        )
        assert complete["data"]["success"] is True
        assert "<artifact_slice" in complete["data"]["result_data"]
        assert long_response not in complete["data"]["result_data"]
        assert complete["data"]["metadata"]["persisted_artifact_id"] == (
            "tool_call_subagent_0001"
        )

    async def test_subagent_instruction_event(self):
        """call_subagent should emit SUBAGENT_INSTRUCTION event."""
        lead_config = _FakeAgentConfig(tools={"call_subagent": "auto"})
        sub_config = _FakeAgentConfig(name="sub_agent", tools={})

        class CallSubagentTool(BaseTool):
            def __init__(self):
                super().__init__(name="call_subagent", description="Dispatch", permission=ToolPermission.AUTO)
            def get_input_schema(self):
                return {"type": "object", "properties": {}, "additionalProperties": True}
            async def execute(self, **p): return ToolResult(success=True, data="ok")
            async def __call__(self, **p): return await self.execute(**p)

        xml = _native_tool_call("call_subagent", agent_name="sub_agent", instruction="do stuff")

        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("sub done"),
            _simple_llm_chunks("lead done"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": lead_config, "sub_agent": sub_config},
            tools={"call_subagent": CallSubagentTool()},
        )

        sub_instr = [
            e for e in result["events"]
            if e.event_type == StreamEventType.SUBAGENT_INSTRUCTION.value
        ]
        assert len(sub_instr) == 1
        assert sub_instr[0].data["instruction"] == "do stuff"


# ============================================================
# TestToolExecution
# ============================================================


class TestToolExecution:

    async def test_simple_tool_execution(self):
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        tool = _FakeTool("my_tool", ToolResult(success=True, data="result_data"))

        xml = _native_tool_call("my_tool", query="test")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("Done with tool"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        starts = [e for e in emitted if e["type"] == "tool_start" and e["data"]["tool"] == "my_tool"]
        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "my_tool"]
        assert len(starts) == 1
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is True

    async def test_search_tools_routed_renders_docs(self):
        # Deferred 工具先经 search_tools 披露完整 native schema。
        from core.effective_toolset import DeferredUnit, EffectiveToolset
        from tools.builtin.search_tools import SearchToolsTool

        agent = _FakeAgentConfig(tools={"search_tools": "auto", "weather": "auto"})
        tools = {
            "search_tools": SearchToolsTool(),
            "weather": _FakeTool("weather", ToolResult(success=True, data="x")),
        }
        calls = _native_tool_call("search_tools", query="select:weather")
        rounds = [
            _tool_call_chunks(calls),
            _simple_llm_chunks("got the schema"),
        ]
        deferred = DeferredUnit(
            name="weather_unit",
            description="Weather tools",
            member_full_names=["weather"],
        )
        effective = EffectiveToolset(
            permissions={
                "search_tools": ToolPermission.AUTO,
                "weather": ToolPermission.AUTO,
            },
            deferred_units={"weather_unit": deferred},
            tool_units={"weather_unit": deferred},
        )

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools=tools,
            effective_toolsets={"lead_agent": effective},
        )

        starts = [e for e in emitted if e["type"] == "tool_start" and e["data"]["tool"] == "search_tools"]
        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "search_tools"]
        assert len(starts) == 1
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is True
        # Result carries the full native declaration and advances sticky disclosure.
        assert "weather" in completes[0]["data"]["result_data"]
        assert "Fake weather" in completes[0]["data"]["result_data"]
        assert result["agent_progressive_state"]["lead_agent"]["disclosed_tools"] == [
            "weather"
        ]

    async def test_wants_context_param_collision_is_tool_failure_not_turn_error(self):
        # 模型误吐 `_context` 参数 → 与引擎注入键撞车 → 绑定 TypeError。协程构造在 try 内,
        # 故降级为单工具失败(tool_complete success=False),turn 照常继续并完成、不掀翻整轮。
        from tools.builtin.search_tools import SearchToolsTool

        agent = _FakeAgentConfig(tools={"search_tools": "auto"})
        tools = {"search_tools": SearchToolsTool()}
        xml = _native_tool_call("search_tools", _context="oops")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("recovered"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools=tools,
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "search_tools"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False     # 工具级失败
        assert result.get("error") is not True              # turn 未被掀翻
        assert result.get("completed") is True              # 第二轮正常收尾

    async def test_search_tools_blocked_when_not_in_toolset(self):
        # 未授 search_tools(无 deferred unit / 未声明)→ 走白名单闸,不路由
        from tools.builtin.search_tools import SearchToolsTool

        agent = _FakeAgentConfig(tools={})  # 空可调集
        tools = {"search_tools": SearchToolsTool()}
        xml = _native_tool_call("search_tools", query="select:weather")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools=tools,
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "search_tools"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "not exposed" in completes[0]["data"]["error"]

    async def test_tool_not_in_whitelist(self):
        agent = _FakeAgentConfig(tools={})  # empty whitelist
        tool = _FakeTool("my_tool")
        xml = _native_tool_call("my_tool")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "my_tool"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "not exposed" in completes[0]["data"]["error"]

    async def test_tool_raises_exception(self):
        agent = _FakeAgentConfig(tools={"bad_tool": "auto"})
        tool = _FailingTool("bad_tool")
        xml = _native_tool_call("bad_tool")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("recovered"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"bad_tool": tool},
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "bad_tool"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "exploded" in completes[0]["data"]["error"]

    async def test_invalid_native_arguments_are_a_bound_tool_failure(self):
        """Invalid JSON is still an accepted call and receives one bound result."""
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        calls = [{
            "id": "call_invalid_json",
            "type": "function",
            "function": {"name": "my_tool", "arguments": "{broken"},
        }]
        rounds = [
            _tool_call_chunks(calls),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": _FakeTool("my_tool")},
        )

        assert result["completed"] is True

        starts = [e for e in emitted if e["type"] == "tool_start"]
        completes = [e for e in emitted if e["type"] == "tool_complete"]
        assert len(starts) == 1
        assert len(completes) == 1
        assert starts[0]["data"]["call_id"] == "call_invalid_json"
        assert completes[0]["data"]["call_id"] == "call_invalid_json"
        assert completes[0]["data"]["success"] is False
        assert "Invalid native tool arguments" in completes[0]["data"]["error"]
        assert emitted.index(starts[0]) < emitted.index(completes[0])


# ============================================================
# TestPermissionInterrupt
# ============================================================


class TestPermissionInterrupt:

    async def test_confirm_tool_emits_permission_request(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _native_tool_call("sensitive_tool", query="test")

        store = InMemoryRuntimeStore()
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", path_events=[])
        emitted = []

        async def _resolve_after_delay():
            """Wait until interrupt exists, then resolve."""
            for _ in range(100):
                if await store.get_interrupt_data("msg-1") is not None:
                    await store.resolve_interrupt("msg-1", {"approved": True})
                    return
                await asyncio.sleep(0.01)

        async def capture_emit(event_dict):
            emitted.append(event_dict)
            if event_dict["type"] == "permission_request":
                asyncio.create_task(_resolve_after_delay())

        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("Done"),
        ]

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config.PERMISSION_TIMEOUT", 5):
            result = await execute_loop(
                state=state,
                agents={"lead_agent": agent},
                tools={"sensitive_tool": tool},
                effective_toolsets=effective_for({"lead_agent": agent}, {"sensitive_tool": tool}),
                hooks=_hooks_from_store(store),
                emit=capture_emit,
            )

        perm_requests = _events_of_type(emitted, "permission_request")
        assert len(perm_requests) == 1

        perm_results = _events_of_type(emitted, "permission_result")
        assert len(perm_results) == 1
        assert perm_results[0]["data"]["approved"] is True

    async def test_denied_tool_not_executed(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _native_tool_call("sensitive_tool", query="test")
        store = InMemoryRuntimeStore()
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", path_events=[])
        emitted = []

        async def _resolve_deny():
            for _ in range(100):
                if await store.get_interrupt_data("msg-1") is not None:
                    await store.resolve_interrupt("msg-1", {"approved": False})
                    return
                await asyncio.sleep(0.01)

        async def capture_emit(event_dict):
            emitted.append(event_dict)
            if event_dict["type"] == "permission_request":
                asyncio.create_task(_resolve_deny())

        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("Ok denied"),
        ]

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config.PERMISSION_TIMEOUT", 5):
            result = await execute_loop(
                state=state,
                agents={"lead_agent": agent},
                tools={"sensitive_tool": tool},
                effective_toolsets=effective_for({"lead_agent": agent}, {"sensitive_tool": tool}),
                hooks=_hooks_from_store(store),
                emit=capture_emit,
            )

        perm_results = _events_of_type(emitted, "permission_result")
        assert perm_results[0]["data"]["approved"] is False

        # Tool complete should show error (denied)
        tool_completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "sensitive_tool"]
        assert any("denied" in str(tc["data"].get("error", "")).lower() for tc in tool_completes)

    async def test_always_allow_skips_subsequent(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _native_tool_call("sensitive_tool", query="test")
        store = InMemoryRuntimeStore()
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", path_events=[])
        emitted = []

        async def _resolve_allow():
            for _ in range(100):
                if await store.get_interrupt_data("msg-1") is not None:
                    await store.resolve_interrupt("msg-1", {"approved": True, "always_allow": True})
                    return
                await asyncio.sleep(0.01)

        async def capture_emit(event_dict):
            emitted.append(event_dict)
            if event_dict["type"] == "permission_request":
                asyncio.create_task(_resolve_allow())

        rounds = [
            _tool_call_chunks(xml),   # first call → interrupt
            _tool_call_chunks(xml),   # second call → should skip interrupt
            _simple_llm_chunks("Done"),
        ]

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config.PERMISSION_TIMEOUT", 5):
            result = await execute_loop(
                state=state,
                agents={"lead_agent": agent},
                tools={"sensitive_tool": tool},
                effective_toolsets=effective_for({"lead_agent": agent}, {"sensitive_tool": tool}),
                hooks=_hooks_from_store(store),
                emit=capture_emit,
            )

        # Only one permission request should have been emitted
        perm_requests = _events_of_type(emitted, "permission_request")
        assert len(perm_requests) == 1
        assert "sensitive_tool" in state["always_allowed_tools"]

    async def test_timeout_treated_as_denied(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _native_tool_call("sensitive_tool", query="test")

        # Use very short timeout
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("timed out"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"sensitive_tool": tool},
            permission_timeout=0,  # immediate timeout
        )

        perm_results = _events_of_type(emitted, "permission_result")
        assert len(perm_results) == 1
        assert perm_results[0]["data"]["approved"] is False

        # 超时也要配对发 TOOL_START + TOOL_COMPLETE，否则 event history 里这次
        # tool_call 没有 TOOL_COMPLETE，下一轮模型看不到结果。
        tool_starts = [
            e for e in emitted
            if e["type"] == "tool_start" and e["data"].get("tool") == "sensitive_tool"
        ]
        tool_completes = [
            e for e in emitted
            if e["type"] == "tool_complete" and e["data"].get("tool") == "sensitive_tool"
        ]
        assert len(tool_starts) == 1
        assert len(tool_completes) == 1
        assert tool_completes[0]["data"]["success"] is False
        timeout_error = tool_completes[0]["data"]["error"].lower()
        assert "without user approval" in timeout_error
        assert "not a tool failure" in timeout_error
        assert "necessary to complete the task" in timeout_error
        # START 必须在 COMPLETE 之前
        assert emitted.index(tool_starts[0]) < emitted.index(tool_completes[0])

# ============================================================
# TestCancellation
# ============================================================


class TestCancellation:

    async def test_cancel_at_loop_top(self):
        """Cancellation flag set before LLM call → immediate exit."""
        store = InMemoryRuntimeStore()
        store._cancellations["msg-1"] = asyncio.Event()
        store._cancellations["msg-1"].set()

        result, emitted, _store = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("should not reach")),
            store=store,
            message_id="msg-1",
        )

        assert result["completed"] is True
        assert result.get("cancelled") is True

    async def test_cancel_between_tools(self):
        """Cancel during tool execution → break out of tool loop."""
        agent = _FakeAgentConfig(tools={"t1": "auto", "t2": "auto"})

        calls = (
            _native_tool_call("t1", param="val1")
            + _native_tool_call("t2", param="val2")
        )

        store = InMemoryRuntimeStore()
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", path_events=[])
        emitted = []

        async def capture_emit(event_dict):
            emitted.append(event_dict)
            # Cancel after first tool completes
            if event_dict["type"] == "tool_complete":
                store._cancellations["msg-1"] = asyncio.Event()
                store._cancellations["msg-1"].set()

        with patch("models.llm.astream_with_retry", _make_fake_stream(_tool_call_chunks(calls))):
            result = await execute_loop(
                state=state,
                agents={"lead_agent": agent},
                tools={"t1": _FakeTool("t1"), "t2": _FakeTool("t2")},
                effective_toolsets=effective_for(
                    {"lead_agent": agent}, {"t1": _FakeTool("t1"), "t2": _FakeTool("t2")}
                ),
                hooks=_hooks_from_store(store),
                emit=capture_emit,
            )

        assert result["completed"] is True
        assert result.get("cancelled") is True

    async def test_cancelled_state_flags(self):
        store = InMemoryRuntimeStore()
        store._cancellations["msg-1"] = asyncio.Event()
        store._cancellations["msg-1"].set()

        result, _, _store = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("x")),
            store=store,
            message_id="msg-1",
        )

        assert result["cancelled"] is True
        assert result["completed"] is True

    async def test_cancel_interrupts_in_flight_tool(self):
        """Cancel while a tool await is in flight → run_cancellable cancels the
        tool task immediately (no waiting out the per-tool timeout), a paired
        TOOL_COMPLETE(success=False) is emitted, and the turn ends CANCELLED."""
        store = InMemoryRuntimeStore()
        message_id = "msg-cancel-tool"
        child_cancelled = asyncio.Event()

        class _HangingTool(BaseTool):
            def __init__(self):
                super().__init__(name="hang", description="hangs", permission=ToolPermission.AUTO)

            def get_input_schema(self):
                return {"type": "object", "properties": {}, "additionalProperties": True}

            async def execute(self, **p):
                # Set the cancel flag once we're in flight, then hang far past
                # anything the test should wait — only task.cancel() ends this.
                await store.request_cancel(message_id)
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    child_cancelled.set()
                    raise
                return ToolResult(success=True, data="never")

            async def __call__(self, **p):
                return await self.execute(**p)

        lead = _FakeAgentConfig(tools={"hang": "auto"})
        result, emitted, _store = await _run_engine(
            _make_fake_stream(_tool_call_chunks(_native_tool_call("hang"))),
            agents={"lead_agent": lead},
            tools={"hang": _HangingTool()},
            message_id=message_id,
            store=store,
            cancel_check_interval=0.01,
        )

        assert result["completed"] is True
        assert result["cancelled"] is True
        assert not result.get("error")
        # The in-flight tool task was actually cancelled, not abandoned
        assert child_cancelled.is_set()
        # START/COMPLETE pairing invariant holds on the cancel path
        starts = _events_of_type(emitted, "tool_start")
        completes = _events_of_type(emitted, "tool_complete")
        assert len(starts) == 1
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "Cancelled by user" in completes[0]["data"]["error"]

    async def test_cancel_mid_stream_plain_text(self):
        """Cancel during a plain-text stream → accumulated prose lands in
        state['response'] and an llm_complete event carries it."""
        store = InMemoryRuntimeStore()
        chunks = [
            {"type": "content", "content": "Hello, "},
            {"type": "content", "content": "this is a partial "},
            {"type": "content", "content": "answer that never finishes"},
        ]
        result, emitted, _ = await _run_engine(
            _make_cancelling_stream(chunks, store, "msg-1", cancel_before_idx=1),
            store=store,
            message_id="msg-1",
            cancel_check_interval=0,
        )

        assert result["cancelled"] is True
        assert result["completed"] is True
        # prose accumulated up to the cancel point becomes the display snapshot
        assert result["response"] == "Hello, this is a partial "
        # llm_complete is the history source of truth — must carry the partial content
        llm_completes = _events_of_type(emitted, StreamEventType.LLM_COMPLETE.value)
        assert len(llm_completes) == 1
        assert llm_completes[0]["data"]["content"] == "Hello, this is a partial "

    async def test_cancel_mid_stream_reasoning_phase(self):
        """Cancel during the reasoning phase (no content chunks yet) → no
        presentable prose, but reasoning is preserved in the llm_complete event."""
        store = InMemoryRuntimeStore()
        chunks = [
            {"type": "reasoning", "content": "Let me think about "},
            {"type": "reasoning", "content": "this problem..."},
        ]
        result, emitted, _ = await _run_engine(
            _make_cancelling_stream(chunks, store, "msg-1", cancel_before_idx=1),
            store=store,
            message_id="msg-1",
            cancel_check_interval=0,
        )

        assert result["cancelled"] is True
        assert not result.get("response")  # reasoning is not a display snapshot
        llm_completes = _events_of_type(emitted, StreamEventType.LLM_COMPLETE.value)
        assert len(llm_completes) == 1
        assert llm_completes[0]["data"]["reasoning_content"] == "Let me think about this problem..."


# ============================================================
# TestRoundLimits
# ============================================================


class TestCancelProbeFailure:
    """check_cancelled 探针故障（Redis 瞬断）的 fail-open 语义（reviewer F2 回归）：
    探针异常绝不伪装成它所落的消费点的故障 —— 工具不被杀、流式不记 "LLM call
    failed"、loop 顶不 ERROR 整个 turn。持续故障的 fail-closed 兜底在
    heartbeat/lease 层（execution_runner 连续失败 → 外部 task.cancel），不在探针。"""

    @staticmethod
    def _hooks_with_flaky_probe(store, flaky):
        return EngineHooks(
            check_cancelled=flaky,
            wait_for_interrupt=store.wait_for_interrupt,
            drain_messages=store.drain_messages,
        )

    async def test_probe_failure_during_tool_keeps_tool_alive(self):
        """探针一次性故障落在工具在飞期间 → 工具不受惊扰、结果如实、turn 正常完成。"""
        store = InMemoryRuntimeStore()
        in_tool = {"armed": False}

        async def flaky(message_id):
            if in_tool["armed"]:
                in_tool["armed"] = False
                raise RuntimeError("cancel store timeout")
            return await store.is_cancelled(message_id)

        class _SlowTool(BaseTool):
            def __init__(self):
                super().__init__(name="slow", description="slow", permission=ToolPermission.AUTO)

            def get_input_schema(self):
                return {"type": "object", "properties": {}, "additionalProperties": True}

            async def execute(self, **p):
                in_tool["armed"] = True  # 让下一拍探针在我们在飞时爆
                await asyncio.sleep(0.05)  # 跨过若干个 0.01s 轮询 tick
                return ToolResult(success=True, data="slow-ok")

            async def __call__(self, **p):
                return await self.execute(**p)

        lead = _FakeAgentConfig(tools={"slow": "auto"})
        rounds = [
            _tool_call_chunks(_native_tool_call("slow")),
            _simple_llm_chunks("Done"),
        ]
        state = create_initial_state(task="t", session_id="s1", message_id="msg-1", path_events=[])
        emitted = []

        async def capture(e):
            emitted.append(e)

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config.CANCEL_CHECK_INTERVAL", 0.01):
            result = await execute_loop(
                state=state,
                agents={"lead_agent": lead},
                tools={"slow": _SlowTool()},
                effective_toolsets=effective_for({"lead_agent": lead}, {"slow": _SlowTool()}),
                hooks=self._hooks_with_flaky_probe(store, flaky),
                emit=capture,
            )

        assert result["completed"] is True
        assert not result.get("cancelled")
        assert not result.get("error")
        assert result["response"] == "Done"
        completes = _events_of_type(emitted, "tool_complete")
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is True
        assert completes[0]["data"]["result_data"] == "slow-ok"
        # 探针确实在工具在飞期间被打过且失败过（armed 被消费）
        assert in_tool["armed"] is False

    async def test_probe_failure_at_loop_top_does_not_error_turn(self):
        """探针故障落在 loop 顶 → turn 不 ERROR，正常跑完。"""
        store = InMemoryRuntimeStore()
        calls = {"n": 0}

        async def flaky(message_id):
            calls["n"] += 1
            if calls["n"] == 1:  # 第一次调用 = while 顶部检查
                raise RuntimeError("cancel store timeout")
            return await store.is_cancelled(message_id)

        state = create_initial_state(task="t", session_id="s1", message_id="msg-1", path_events=[])

        with patch("models.llm.astream_with_retry", _make_fake_stream(_simple_llm_chunks("Done!"))):
            agents = {"lead_agent": _FakeAgentConfig()}
            result = await execute_loop(
                state=state,
                agents=agents,
                tools={},
                effective_toolsets=effective_for(agents, {}),
                hooks=self._hooks_with_flaky_probe(store, flaky),
                emit=None,
            )

        assert result["completed"] is True
        assert not result.get("error")
        assert result["response"] == "Done!"

    async def test_probe_failure_mid_stream_not_llm_failure(self):
        """探针故障落在 LLM 流式轮询 → 不得被记成 "LLM call failed" 的 ERROR。"""
        store = InMemoryRuntimeStore()
        calls = {"n": 0}

        async def flaky(message_id):
            calls["n"] += 1
            if calls["n"] == 2:  # 1=loop 顶；2=第一个 chunk 的流式轮询
                raise RuntimeError("cancel store timeout")
            return await store.is_cancelled(message_id)

        state = create_initial_state(task="t", session_id="s1", message_id="msg-1", path_events=[])

        with patch("models.llm.astream_with_retry", _make_fake_stream(_simple_llm_chunks("Done!"))), \
             patch("core.engine.config.CANCEL_CHECK_INTERVAL", 0):
            agents = {"lead_agent": _FakeAgentConfig()}
            result = await execute_loop(
                state=state,
                agents=agents,
                tools={},
                effective_toolsets=effective_for(agents, {}),
                hooks=self._hooks_with_flaky_probe(store, flaky),
                emit=None,
            )

        assert calls["n"] >= 2  # 故障确实落在流式轮询上
        assert result["completed"] is True
        assert not result.get("error")
        assert result["response"] == "Done!"


class TestRoundLimits:

    async def test_max_tool_rounds_adds_tool_budget_reminder(self):
        """After max_tool_rounds, a <tool_budget> wrap-up nudge is folded into the
        reminder (merged into the last user message) — no longer a separate system message."""
        agent = _FakeAgentConfig(tools={"my_tool": "auto"}, max_tool_rounds=1)
        tool = _FakeTool("my_tool")

        xml = _native_tool_call("my_tool", query="test")

        captured_messages = []
        call_idx = {"n": 0}

        async def intercepting_stream(messages, **kwargs):
            captured_messages.append(list(messages))
            call_idx["n"] += 1
            idx = min(call_idx["n"] - 1, 1)
            chunks = [
                _tool_call_chunks(xml),
                _simple_llm_chunks("Done"),
            ][idx]
            for c in chunks:
                yield c

        result, emitted, store = await _run_engine(
            intercepting_stream,
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        # Second call: the budget nudge rides in the reminder on the last user message
        assert len(captured_messages) >= 2
        last_call_msgs = captured_messages[-1]
        joined = "\n".join(
            m["content"] if isinstance(m["content"], str) else str(m["content"])
            for m in last_call_msgs
        )
        assert "<tool_budget>" in joined
        assert "Tool-round budget reached" in joined


# ============================================================
# TestPendingMessageDrain
# ============================================================


class TestPendingMessageDrain:

    async def test_lead_drains_before_completing(self):
        """If messages arrive during last LLM call, lead should continue instead of completing."""
        store = InMemoryRuntimeStore()
        call_count = {"n": 0}

        async def injecting_stream(messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Inject a message during first LLM call
                await store.inject_message("msg-1", "injected content")
                for c in _simple_llm_chunks("first response"):
                    yield c
            else:
                for c in _simple_llm_chunks("final response"):
                    yield c

        result, emitted, _store = await _run_engine(
            injecting_stream,
            store=store,
            message_id="msg-1",
        )

        assert result["completed"] is True
        assert result["response"] == "final response"

        # Should have queued_message event
        queued = [e for e in emitted if e["type"] == StreamEventType.QUEUED_MESSAGE.value]
        assert len(queued) >= 1


# ============================================================
# TestMetrics
# ============================================================


class TestMetrics:

    async def test_metrics_timestamps(self):
        result, _, store = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("ok"))
        )

        metrics = result["execution_metrics"]
        assert metrics["started_at"] is not None
        assert metrics["completed_at"] is not None
        assert metrics["total_duration_ms"] is not None
        assert metrics["total_duration_ms"] >= 0

    async def test_token_usage_aggregation(self):
        """Multi-round token usage should be aggregated."""
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        tool = _FakeTool("my_tool")
        xml = _native_tool_call("my_tool", query="test")

        rounds = [
            _tool_call_chunks(xml, input_tokens=100, output_tokens=50),
            _simple_llm_chunks("Done", input_tokens=200, output_tokens=30),
        ]

        result, _, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        total = result["execution_metrics"]["total_token_usage"]
        assert total["input_tokens"] == 300
        assert total["output_tokens"] == 80
        assert total["total_tokens"] == 380

    async def test_per_turn_token_metrics(self):
        """first_input_tokens, last_output_tokens, last_input_tokens should be tracked for lead_agent."""
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        tool = _FakeTool("my_tool")
        xml = _native_tool_call("my_tool", query="test")

        rounds = [
            _tool_call_chunks(xml, input_tokens=100, output_tokens=50),
            _simple_llm_chunks("Done", input_tokens=200, output_tokens=30),
        ]

        result, _, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        metrics = result["execution_metrics"]
        assert metrics["first_input_tokens"] == 100
        assert metrics["last_input_tokens"] == 200
        assert metrics["last_output_tokens"] == 30

    async def test_token_usage_estimated_when_provider_returns_none(self):
        """When provider doesn't return usage, llm.py estimates via token_counter."""
        from models.llm import astream_with_retry

        # Mock acompletion to return a stream with no usage
        async def fake_response():
            """Simulate a stream with content but no usage."""
            from unittest.mock import MagicMock

            chunk = MagicMock()
            chunk.usage = None
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "Hello"
            chunk.choices[0].delta.reasoning_content = None
            yield chunk

            # Final chunk with no choices, no usage
            end = MagicMock()
            end.usage = None
            end.choices = []
            yield end

        with patch("models.llm.acompletion", return_value=fake_response()), \
             patch("litellm.token_counter", return_value=42):
            chunks = []
            async for chunk in astream_with_retry(
                [{"role": "user", "content": "hi"}], model="openai/fake-model"
            ):
                chunks.append(chunk)

        # Should have usage and final chunks with estimated values
        usage_chunks = [c for c in chunks if c["type"] == "usage"]
        assert len(usage_chunks) == 1
        assert usage_chunks[0]["token_usage"]["prompt_tokens"] == 42
        assert usage_chunks[0]["token_usage"]["completion_tokens"] == 42

        final_chunks = [c for c in chunks if c["type"] == "final"]
        assert len(final_chunks) == 1
        assert final_chunks[0]["token_usage"]["prompt_tokens"] == 42


# ============================================================
# TestInEngineCompaction
# ============================================================


class TestInEngineCompaction:
    """
    Integration coverage for the engine → CompactionRunner wiring.

    These tests intentionally go through execute_loop (not just
    CompactionRunner.maybe_trigger) so that deleting the `await
    compaction_runner.maybe_trigger(...)` call in src/core/engine.py
    would fail CI — the unit tests alone would not catch that regression.
    """

    async def test_over_threshold_triggers_compaction(self):
        """Lead LLM returns usage > threshold → compaction_start + compaction_summary in state + SSE."""
        lead = _FakeAgentConfig(tools={})
        compact = _FakeAgentConfig(name="compact_agent", role_prompt="Compactor.", tools={})

        # Round 1: lead returns big usage — triggers compaction after this call
        # Round 2: compact_agent (same astream_with_retry patch) returns a summary
        rounds = [
            _simple_llm_chunks("Done", input_tokens=80, output_tokens=30),
            [
                {"type": "content", "content": "<summary>compacted prior turn</summary>"},
                {"type": "usage", "token_usage": {
                    "prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70,
                }},
                {"type": "final", "content": "<summary>compacted prior turn</summary>",
                 "reasoning_content": None, "token_usage": {
                    "prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70,
                 }},
            ],
        ]

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            result, emitted, _ = await _run_engine(
                _make_fake_stream_sequence(rounds),
                agents={"lead_agent": lead, "compact_agent": compact},
            )

        # Both events should end up in persisted state
        event_types = [e.event_type for e in result["events"]]
        assert "compaction_start" in event_types
        assert "compaction_summary" in event_types

        # And both should have been emitted to SSE
        emitted_types = [e["type"] for e in emitted]
        assert "compaction_start" in emitted_types
        assert "compaction_summary" in emitted_types

        # compaction_summary must be tagged with the triggering agent
        summary_ev = next(e for e in result["events"] if e.event_type == "compaction_summary")
        assert summary_ev.agent_name == "lead_agent"
        # Content = memory-aid frame + raw summary from compact_agent
        assert summary_ev.data["content"].startswith("[Prior conversation has been compacted")
        assert "compacted prior turn" in summary_ev.data["content"]
        assert summary_ev.data["model"] == "openai/fake-model"
        assert summary_ev.data["error"] is None

    async def test_under_threshold_no_compaction(self):
        """Usage below threshold → no compaction events appear."""
        lead = _FakeAgentConfig(tools={})
        compact = _FakeAgentConfig(name="compact_agent", role_prompt="Compactor.", tools={})

        rounds = [_simple_llm_chunks("Done", input_tokens=10, output_tokens=5)]

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 1000):
            result, emitted, _ = await _run_engine(
                _make_fake_stream_sequence(rounds),
                agents={"lead_agent": lead, "compact_agent": compact},
            )

        event_types = [e.event_type for e in result["events"]]
        assert "compaction_start" not in event_types
        assert "compaction_summary" not in event_types

        emitted_types = [e["type"] for e in emitted]
        assert "compaction_start" not in emitted_types
        assert "compaction_summary" not in emitted_types

    async def test_cancel_during_compaction_routes_to_cancelled(self):
        """User cancel landing inside the compaction LLM call (previously the
        longest cancel blind window: COMPACTION_TIMEOUT) → the in-flight compact
        call is task-cancelled, a paired success=False compaction_summary is
        appended, and the turn ends CANCELLED — NOT ERROR."""
        store = InMemoryRuntimeStore()
        message_id = "msg-cancel-compact"
        compact_call_cancelled = asyncio.Event()
        calls = {"n": 0}

        async def fake_llm(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # Lead call: usage over threshold → compaction triggers next
                for c in _simple_llm_chunks("Done", input_tokens=80, output_tokens=30):
                    yield c
            else:
                # Compact call: set the cancel flag once in flight, then hang
                await store.request_cancel(message_id)
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    compact_call_cancelled.set()
                    raise
                yield {"type": "content", "content": "never"}

        lead = _FakeAgentConfig(tools={})
        compact = _FakeAgentConfig(name="compact_agent", role_prompt="Compactor.", tools={})

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100), \
             patch("core.compaction_runner.config.CANCEL_CHECK_INTERVAL", 0.01):
            result, emitted, _ = await _run_engine(
                fake_llm,
                agents={"lead_agent": lead, "compact_agent": compact},
                message_id=message_id,
                store=store,
            )

        assert result["completed"] is True
        assert result["cancelled"] is True
        assert not result.get("error")
        assert compact_call_cancelled.is_set()

        # compaction_start has its paired success=False terminator (no boundary
        # for EventHistory, but the event stream stays well-formed)
        event_types = [e.event_type for e in result["events"]]
        assert "compaction_start" in event_types
        summary_ev = next(e for e in result["events"] if e.event_type == "compaction_summary")
        assert summary_ev.data["success"] is False

    async def test_no_compact_agent_silently_skips_over_threshold(self):
        """Over threshold but compact_agent not registered → no crash, no compaction events."""
        lead = _FakeAgentConfig(tools={})
        # Note: no compact_agent in the agents dict

        rounds = [_simple_llm_chunks("Done", input_tokens=80, output_tokens=30)]

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            result, emitted, _ = await _run_engine(
                _make_fake_stream_sequence(rounds),
                agents={"lead_agent": lead},
            )

        # Engine should complete normally
        assert result["completed"] is True
        assert result["response"] == "Done"

        event_types = [e.event_type for e in result["events"]]
        assert "compaction_summary" not in event_types

    async def test_force_compact_fires_below_threshold(self):
        """
        force_compact=True bypasses the token threshold: compaction fires after
        the first lead LLM call even when usage is far below the threshold.
        Anti-regression for the manual-compaction trigger.
        """
        lead = _FakeAgentConfig(tools={})
        compact = _FakeAgentConfig(name="compact_agent", role_prompt="Compactor.", tools={})

        rounds = [
            _simple_llm_chunks("Done", input_tokens=10, output_tokens=5),
            [
                {"type": "content", "content": "<summary>manually compacted</summary>"},
                {"type": "usage", "token_usage": {
                    "prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70,
                }},
                {"type": "final", "content": "<summary>manually compacted</summary>",
                 "reasoning_content": None, "token_usage": {
                    "prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70,
                 }},
            ],
        ]

        # Threshold deliberately way above usage — only the force flag can fire compaction.
        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100_000):
            result, _, _ = await _run_engine(
                _make_fake_stream_sequence(rounds),
                agents={"lead_agent": lead, "compact_agent": compact},
                force_compact=True,
            )

        event_types = [e.event_type for e in result["events"]]
        assert "compaction_start" in event_types
        assert "compaction_summary" in event_types

    async def test_force_compact_with_tool_calls_keeps_tail_fresh(self):
        """
        Partial-compaction intent (the engineering choice documented at the
        engine.py maybe_trigger call site): when forced compaction fires after
        a lead response that carries a tool call, the in-flight work (tool
        result + the continuation LLM call) must land AFTER the
        compaction_summary boundary, NOT be folded into it.

        Reviewer asked for this case explicitly — it pins down that "保留 last
        turn 在干什么" survives moving the manual compaction feature around.
        """
        echo_tool = _FakeTool("echo", ToolResult(success=True, data="echo result"))
        lead = _FakeAgentConfig(tools={"echo": "auto"})
        compact = _FakeAgentConfig(name="compact_agent", role_prompt="Compactor.", tools={})

        rounds = [
            # R1: lead calls a tool. Forced compaction fires after this call
            # (using R1's input_tokens as the measurement); R1 itself is folded.
            _tool_call_chunks(_native_tool_call("echo", text="hi"), input_tokens=10, output_tokens=5),
            # R2: compact_agent produces the summary.
            [
                {"type": "content", "content": "<summary>compacted prior + R1</summary>"},
                {"type": "usage", "token_usage": {
                    "prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70,
                }},
                {"type": "final", "content": "<summary>compacted prior + R1</summary>",
                 "reasoning_content": None, "token_usage": {
                    "prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70,
                 }},
            ],
            # R3: lead's continuation after tool_result — must end up AFTER the summary.
            _simple_llm_chunks("Final after tool", input_tokens=15, output_tokens=8),
        ]

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100_000):
            result, _, _ = await _run_engine(
                _make_fake_stream_sequence(rounds),
                agents={"lead_agent": lead, "compact_agent": compact},
                tools={"echo": echo_tool},
                force_compact=True,
            )

        event_types = [e.event_type for e in result["events"]]
        summary_idx = event_types.index("compaction_summary")

        # Tail AFTER the summary boundary must contain the tool execution + the
        # final lead response — proving that the in-flight tool work is NOT
        # folded into the summary (the whole point of the partial-compaction
        # design). The order matters: it must be summary FIRST, then tail.
        tail_types = event_types[summary_idx + 1:]
        assert "tool_complete" in tail_types, (
            f"tool_complete should land after compaction_summary boundary; "
            f"got tail={tail_types}"
        )
        assert "llm_complete" in tail_types, (
            f"continuation llm_complete should land after compaction_summary boundary; "
            f"got tail={tail_types}"
        )
        # And the final response should be the post-summary one, not the folded R1.
        assert result["response"] == "Final after tool"

    async def test_force_compact_final_response_writes_summary_size_to_last_input(self):
        """
        P2 fix: when compaction fires on the final (no-tool-call) lead response,
        loop ends right after — there's no subsequent real LLM call to overwrite
        `last_input_tokens` via engine.py:425. The unconditional write inside
        compaction_runner therefore sticks, so the persisted value equals the
        compact call's output_tokens (= summary size, a real measured number).

        This is the only window where the composer's context-usage gauge would
        otherwise show the stale pre-compaction input ("near threshold right
        after the user explicitly compacted"). Verifying the value here pins
        down both the fix and the asymmetry — the write is harmless in any
        other case because a real later call overwrites it.
        """
        lead = _FakeAgentConfig(tools={})
        compact = _FakeAgentConfig(name="compact_agent", role_prompt="Compactor.", tools={})

        rounds = [
            # R1: final answer (no tool call). Forced compaction fires after this;
            # since R1 had no tool calls the loop ends with no later LLM call.
            _simple_llm_chunks("Final", input_tokens=12345, output_tokens=42),
            # R2: compact_agent — its completion_tokens=99 is the summary size
            # that should land in last_input_tokens.
            [
                {"type": "content", "content": "<summary>compacted</summary>"},
                {"type": "usage", "token_usage": {
                    "prompt_tokens": 500, "completion_tokens": 99, "total_tokens": 599,
                }},
                {"type": "final", "content": "<summary>compacted</summary>",
                 "reasoning_content": None, "token_usage": {
                    "prompt_tokens": 500, "completion_tokens": 99, "total_tokens": 599,
                 }},
            ],
        ]

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100_000):
            result, _, _ = await _run_engine(
                _make_fake_stream_sequence(rounds),
                agents={"lead_agent": lead, "compact_agent": compact},
                force_compact=True,
            )

        # Pre-condition: compaction actually ran.
        event_types = [e.event_type for e in result["events"]]
        assert "compaction_summary" in event_types

        # P2 invariant: last_input_tokens reflects the post-compaction context
        # estimate (= compact call's output_tokens), NOT the pre-compaction R1
        # input (12345) that engine.py:425 wrote first.
        assert result["execution_metrics"]["last_input_tokens"] == 99, (
            f"expected last_input_tokens to be the summary size (99); "
            f"got {result['execution_metrics']['last_input_tokens']} — "
            "if this is 12345, the post-compaction write in compaction_runner is gone "
            "and the gauge will show stale pre-compaction tokens."
        )


# ============================================================
# TestSkillActivation (C-2: per-agent skill state + skill_grants merge)
# ============================================================


class _ActivatingTool(BaseTool):
    """read_skill 替身:返回正文 + activated_skill metadata(引擎据此激活)。"""

    def __init__(self, slug: str):
        super().__init__(name="read_skill", description="read a skill",
                         permission=ToolPermission.AUTO)
        self._slug = slug

    def get_input_schema(self):
        return {"type": "object", "properties": {}, "additionalProperties": True}

    async def execute(self, **params) -> ToolResult:
        return ToolResult(success=True, data="GUIDANCE",
                          metadata={"activated_skill": self._slug})

    async def __call__(self, **params) -> ToolResult:
        return await self.execute(**params)


class TestSkillActivation:
    async def test_read_skill_activates_and_grants_tool_mid_turn(self):
        from core.effective_toolset import EffectiveToolset, SkillGrant

        read_skill = _ActivatingTool("s")
        granted = _FakeTool("granted_tool", ToolResult(success=True, data="granted-ran"))
        tools = {"read_skill": read_skill, "granted_tool": granted}

        # 初始可调集只有 read_skill;granted_tool 不在(模拟 agent disabled),仅在 skill_grants
        eff = EffectiveToolset(
            permissions={"read_skill": ToolPermission.AUTO},
            skill_grants={"s": SkillGrant(permissions={"granted_tool": ToolPermission.AUTO})},
        )
        agents = {"lead_agent": _FakeAgentConfig()}
        effective_toolsets = {"lead_agent": eff}

        # 三轮:① 调 read_skill ② 调 granted_tool(激活后才可调)③ 收尾
        rounds = [
            _tool_call_chunks(_native_tool_call("read_skill", slug="s")),
            _tool_call_chunks(_native_tool_call("granted_tool")),
            _simple_llm_chunks("Done"),
        ]
        state = create_initial_state(task="hi", session_id="sess", message_id="msg-act")
        store = InMemoryRuntimeStore()
        emitted = []

        async def capture_emit(e):
            emitted.append(e)

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config") as mock_config:
            from config import config as real_config
            for attr in dir(real_config):
                if attr.isupper():
                    setattr(mock_config, attr, getattr(real_config, attr))
            result = await execute_loop(
                state=state, agents=agents, tools=tools,
                effective_toolsets=effective_toolsets,
                hooks=_hooks_from_store(store), emit=capture_emit,
            )

        # 激活持久进 state(回合末由 controller 写 metadata)
        assert result["agent_progressive_state"]["lead_agent"]["active_skills"] == ["s"]
        # granted_tool 被激活后翻进可调集
        assert "granted_tool" in eff
        # 两个工具都真执行成功(granted_tool 没被白名单闸拒)
        completes = {e["data"]["tool"]: e["data"]["success"]
                     for e in emitted if e["type"] == "tool_complete"}
        assert completes.get("read_skill") is True
        assert completes.get("granted_tool") is True

    async def test_failed_read_skill_does_not_activate(self):
        from core.effective_toolset import EffectiveToolset, SkillGrant

        class _FailRead(BaseTool):
            def __init__(self):
                super().__init__(name="read_skill", description="x",
                                 permission=ToolPermission.AUTO)
            def get_input_schema(self):
                return {"type": "object", "properties": {}, "additionalProperties": True}
            async def execute(self, **p):
                return ToolResult(success=False, error="nope",
                                  metadata={"activated_skill": "s"})
            async def __call__(self, **p): return await self.execute(**p)

        eff = EffectiveToolset(
            permissions={"read_skill": ToolPermission.AUTO},
            skill_grants={"s": SkillGrant(permissions={"granted_tool": ToolPermission.AUTO})},
        )
        state = create_initial_state(task="hi", session_id="sess", message_id="msg-f")
        rounds = [_tool_call_chunks(_native_tool_call("read_skill", slug="s")),
                  _simple_llm_chunks("Done")]
        store = InMemoryRuntimeStore()

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config") as mock_config:
            from config import config as real_config
            for attr in dir(real_config):
                if attr.isupper():
                    setattr(mock_config, attr, getattr(real_config, attr))
            result = await execute_loop(
                state=state, agents={"lead_agent": _FakeAgentConfig()},
                tools={"read_skill": _FailRead()},
                effective_toolsets={"lead_agent": eff},
                hooks=_hooks_from_store(store), emit=lambda e: asyncio.sleep(0),
            )

        # 失败调用不激活(only on success)
        assert result["agent_progressive_state"].get("lead_agent", {}).get(
            "active_skills", []
        ) == []
        assert "granted_tool" not in eff

    async def test_button_activation_injects_body_into_user_input(self):
        """C-3:用户按钮激活 → controller 传 activated_skill_bodies → engine 注入 USER_INPUT
        正文(仅 LLM 可见,同 force_compact/上传路径),让模型即刻看到 skill 指令。"""
        from core.effective_toolset import EffectiveToolset
        from core.skill_guidance import render_skill_guidance

        state = create_initial_state(
            task="use it", session_id="sess", message_id="msg-b",
            agent_progressive_state={
                "lead_agent": {"active_skills": ["s"], "disclosed_tools": []}
            },
            activated_skill_bodies=[
                {
                    "slug": "s",
                    "name": "My Skill",
                    "body": render_skill_guidance(
                        "DO THE THING", has_extra_files=True
                    ),
                }
            ],
        )
        rounds = [_simple_llm_chunks("Done")]
        store = InMemoryRuntimeStore()

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config") as mock_config:
            from config import config as real_config
            for attr in dir(real_config):
                if attr.isupper():
                    setattr(mock_config, attr, getattr(real_config, attr))
            result = await execute_loop(
                state=state, agents={"lead_agent": _FakeAgentConfig()}, tools={},
                effective_toolsets={"lead_agent": EffectiveToolset(permissions={})},
                hooks=_hooks_from_store(store), emit=lambda e: asyncio.sleep(0),
            )

        user_inputs = [
            e for e in result["events"]
            if e.event_type == StreamEventType.USER_INPUT.value
        ]
        assert user_inputs, "expected a USER_INPUT event"
        content = user_inputs[-1].data["content"]
        assert "use it" in content              # 原始输入保留
        assert "My Skill" in content            # skill 名注入
        assert "DO THE THING" in content        # skill 正文注入
        assert "mount_skill" in content          # 与 read_skill 同一条件化提醒

    async def test_activation_only_turn_body_becomes_content(self):
        """纯激活轮(无文本):skill 正文即 USER_INPUT 正文,让 lead 总有可回应输入。"""
        from core.effective_toolset import EffectiveToolset

        state = create_initial_state(
            task="", session_id="sess", message_id="msg-b2",
            agent_progressive_state={
                "lead_agent": {"active_skills": ["s"], "disclosed_tools": []}
            },
            activated_skill_bodies=[{"slug": "s", "name": "S", "body": "BODY-ONLY"}],
        )
        rounds = [_simple_llm_chunks("Done")]
        store = InMemoryRuntimeStore()

        with patch("models.llm.astream_with_retry", _make_fake_stream_sequence(rounds)), \
             patch("core.engine.config") as mock_config:
            from config import config as real_config
            for attr in dir(real_config):
                if attr.isupper():
                    setattr(mock_config, attr, getattr(real_config, attr))
            result = await execute_loop(
                state=state, agents={"lead_agent": _FakeAgentConfig()}, tools={},
                effective_toolsets={"lead_agent": EffectiveToolset(permissions={})},
                hooks=_hooks_from_store(store), emit=lambda e: asyncio.sleep(0),
            )

        content = [
            e for e in result["events"]
            if e.event_type == StreamEventType.USER_INPUT.value
        ][-1].data["content"]
        assert "BODY-ONLY" in content


# ============================================================
# TestMixedSerialDelegation — 同轮 [tool, subagent, subagent, tool] 混合串行
# ============================================================


class TestMixedSerialDelegation:
    """call_subagent 原地递归后:同轮混合调用按自然序串行执行,不再 sort-to-end
    + break;turn 在子 agent 内终止(cancel / error)则剩余工具不执行。"""

    @staticmethod
    def _real_call_subagent(valid_agents):
        from tools.builtin.call_subagent import CallSubagentTool
        return CallSubagentTool(valid_agents=valid_agents)

    async def test_mixed_order_natural_serial(self):
        """[tool_a, sub_a, sub_b, tool_b] 按模型给出的顺序执行,结果按序回填。"""
        lead = _FakeAgentConfig(
            tools={"call_subagent": "auto", "tool_a": "auto", "tool_b": "auto"}
        )
        sub_a = _FakeAgentConfig(name="sub_a", tools={})
        sub_b = _FakeAgentConfig(name="sub_b", tools={})

        response_r1 = (
            _native_tool_call("tool_a")
            + _native_tool_call("call_subagent", agent_name="sub_a", instruction="task A")
            + _native_tool_call("call_subagent", agent_name="sub_b", instruction="task B")
            + _native_tool_call("tool_b")
        )
        rounds = [
            _tool_call_chunks(response_r1),       # lead round 1: 4 calls
            _simple_llm_chunks("result from A"),  # sub_a
            _simple_llm_chunks("result from B"),  # sub_b
            _simple_llm_chunks("Final answer"),   # lead round 2
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": lead, "sub_a": sub_a, "sub_b": sub_b},
            tools={
                "tool_a": _FakeTool("tool_a", ToolResult(success=True, data="A-ok")),
                "tool_b": _FakeTool("tool_b", ToolResult(success=True, data="B-ok")),
                "call_subagent": self._real_call_subagent(["sub_a", "sub_b"]),
            },
        )

        assert result["completed"] is True
        assert result["response"] == "Final answer"

        # 自然序:call_subagent 不再被排到末尾
        start_names = [e["data"]["tool"] for e in _events_of_type(emitted, "tool_start")]
        assert start_names == ["tool_a", "call_subagent", "call_subagent", "tool_b"]

        # agent 泳道按执行序展开
        agent_starts = [e["agent"] for e in _events_of_type(emitted, "agent_start")]
        assert agent_starts == ["lead_agent", "sub_a", "sub_b", "lead_agent"]

        # 两个 subagent 的结果按序回填成 <subagent_result>
        sub_results = [
            e["data"]["result_data"]
            for e in _events_of_type(emitted, "tool_complete")
            if e["data"].get("tool") == "call_subagent"
        ]
        assert len(sub_results) == 2
        assert 'agent="sub_a"' in sub_results[0] and "result from A" in sub_results[0]
        assert 'agent="sub_b"' in sub_results[1] and "result from B" in sub_results[1]

        # 事件序 = 执行序:tool_b 的 START 在 sub_b 完成之后
        idx_sub_b_done = next(
            i for i, e in enumerate(emitted)
            if e["type"] == "agent_complete" and e.get("agent") == "sub_b"
        )
        idx_tool_b = next(
            i for i, e in enumerate(emitted)
            if e["type"] == "tool_start" and (e.get("data") or {}).get("tool") == "tool_b"
        )
        assert idx_tool_b > idx_sub_b_done

        # 两条 SUBAGENT_INSTRUCTION 各归其主
        instr = [
            e for e in result["events"]
            if e.event_type == StreamEventType.SUBAGENT_INSTRUCTION.value
        ]
        assert [e.agent_name for e in instr] == ["sub_a", "sub_b"]

    async def test_cancel_during_subagent_skips_remaining_tools(self):
        """cancel 落在子 agent 的 LLM 流期间:call_subagent 留 orphan TOOL_START
        (无 COMPLETE = 调用未完成),同轮剩余工具不执行,turn 收口 CANCELLED。"""
        lead = _FakeAgentConfig(tools={"call_subagent": "auto", "tool_b": "auto"})
        sub_a = _FakeAgentConfig(name="sub_a", tools={})
        store = InMemoryRuntimeStore()

        r1 = (
            _native_tool_call("call_subagent", agent_name="sub_a", instruction="task A")
            + _native_tool_call("tool_b")
        )
        call_count = {"n": 0}

        async def fake(messages, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            if idx == 0:
                for c in _tool_call_chunks(r1):
                    yield c
            else:
                chunks = _simple_llm_chunks("partial sub answer")
                for i, c in enumerate(chunks):
                    if i == 1:
                        store._cancellations["msg-1"] = asyncio.Event()
                        store._cancellations["msg-1"].set()
                    yield c

        result, emitted, _ = await _run_engine(
            fake,
            agents={"lead_agent": lead, "sub_a": sub_a},
            tools={
                "call_subagent": self._real_call_subagent(["sub_a"]),
                "tool_b": _FakeTool("tool_b"),
            },
            store=store,
            cancel_check_interval=0,
        )

        assert result["completed"] is True
        assert result.get("cancelled") is True
        start_names = [e["data"]["tool"] for e in _events_of_type(emitted, "tool_start")]
        assert start_names == ["call_subagent"]  # tool_b 从未启动
        sub_completes = [
            e for e in _events_of_type(emitted, "tool_complete")
            if e["data"].get("tool") == "call_subagent"
        ]
        assert sub_completes == []

    async def test_subagent_llm_error_skips_remaining_tools(self):
        """子 agent 的 LLM 失败:error 归因到子 agent,同轮剩余工具不执行,
        turn 收口 ERROR(record-not-emit,详见 decide_terminal)。"""
        lead = _FakeAgentConfig(tools={"call_subagent": "auto", "tool_b": "auto"})
        sub_a = _FakeAgentConfig(name="sub_a", tools={})

        r1 = (
            _native_tool_call("call_subagent", agent_name="sub_a", instruction="task A")
            + _native_tool_call("tool_b")
        )
        call_count = {"n": 0}

        async def fake(messages, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            if idx == 0:
                for c in _tool_call_chunks(r1):
                    yield c
            else:
                raise RuntimeError("llm boom")
                yield  # pragma: no cover — 保持 async generator 形态

        result, emitted, _ = await _run_engine(
            fake,
            agents={"lead_agent": lead, "sub_a": sub_a},
            tools={
                "call_subagent": self._real_call_subagent(["sub_a"]),
                "tool_b": _FakeTool("tool_b"),
            },
        )

        assert result["error"] is True
        assert result["error_detail"]["agent"] == "sub_a"
        assert "llm boom" in result["error_detail"]["error"]
        start_names = [e["data"]["tool"] for e in _events_of_type(emitted, "tool_start")]
        assert start_names == ["call_subagent"]  # tool_b 从未启动

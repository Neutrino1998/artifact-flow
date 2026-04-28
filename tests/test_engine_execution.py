"""
Engine execution flow tests.

Covers: agent routing, tool execution, cancellation, permission interrupts,
subagent routing, round limits, pending message drain, and metrics.

Mock strategy: patch("models.llm.astream_with_retry") + real RuntimeStore.
"""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from core.engine import EngineHooks, create_initial_state, execute_loop
from core.events import StreamEventType, ExecutionEvent
from api.services.runtime_store import InMemoryRuntimeStore
from tools.base import BaseTool, ToolPermission, ToolResult


# ============================================================
# Helpers
# ============================================================


@dataclass
class _FakeAgentConfig:
    name: str = "lead_agent"
    description: str = "test lead"
    tools: dict = field(default_factory=dict)
    model: str = "fake-model"
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


def _tool_call_xml(tool_name: str, **params) -> str:
    """Build correct tool_call XML for the parser: <name>...</name><params>...</params>."""
    xml = f"<tool_call>\n<name>{tool_name}</name>\n"
    if params:
        xml += "<params>\n"
        for k, v in params.items():
            xml += f"<{k}><![CDATA[{v}]]></{k}>\n"
        xml += "</params>\n"
    xml += "</tool_call>"
    return xml


def _tool_call_chunks(xml: str, input_tokens: int = 10, output_tokens: int = 5):
    """Build LLM chunks whose response includes a tool_call XML block."""
    return _simple_llm_chunks(xml, input_tokens, output_tokens)


class _FakeTool(BaseTool):
    """Configurable fake tool for testing."""

    def __init__(self, name: str, result: ToolResult = None, permission: ToolPermission = ToolPermission.AUTO):
        super().__init__(name=name, description=f"Fake {name}", permission=permission)
        self._result = result or ToolResult(success=True, data="ok")

    def get_parameters(self):
        return []

    async def execute(self, **params) -> ToolResult:
        return self._result

    async def __call__(self, **params) -> ToolResult:
        return await self.execute(**params)


class _FailingTool(BaseTool):
    """Tool that raises an exception."""

    def __init__(self, name: str):
        super().__init__(name=name, description=f"Failing {name}", permission=ToolPermission.AUTO)

    def get_parameters(self):
        return []

    async def execute(self, **params) -> ToolResult:
        raise RuntimeError("tool exploded")

    async def __call__(self, **params) -> ToolResult:
        return await self.execute(**params)


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
):
    """Helper to run engine with given LLM factory and return (state, emitted)."""
    state = create_initial_state(
        task=task,
        session_id="sess-1",
        message_id=message_id,
        path_events=path_events or [],
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
        result = await execute_loop(
            state=state,
            agents=agents,
            tools=tools or {},
            hooks=_hooks_from_store(store),
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

        call_subagent_xml = _tool_call_xml(
            "call_subagent",
            agent_name="search_agent",
            instruction="find stuff",
        )

        class CallSubagentTool(BaseTool):
            def __init__(self):
                super().__init__(name="call_subagent", description="Dispatch", permission=ToolPermission.AUTO)
            def get_parameters(self): return []
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

    async def test_subagent_instruction_event(self):
        """call_subagent should emit SUBAGENT_INSTRUCTION event."""
        lead_config = _FakeAgentConfig(tools={"call_subagent": "auto"})
        sub_config = _FakeAgentConfig(name="sub_agent", tools={})

        class CallSubagentTool(BaseTool):
            def __init__(self):
                super().__init__(name="call_subagent", description="Dispatch", permission=ToolPermission.AUTO)
            def get_parameters(self): return []
            async def execute(self, **p): return ToolResult(success=True, data="ok")
            async def __call__(self, **p): return await self.execute(**p)

        xml = _tool_call_xml("call_subagent", agent_name="sub_agent", instruction="do stuff")

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

        xml = _tool_call_xml("my_tool", query="test")
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

    async def test_tool_not_found(self):
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        xml = _tool_call_xml("my_tool")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={},  # no tools registered
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "my_tool"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "not found" in completes[0]["data"]["error"]

    async def test_tool_not_in_whitelist(self):
        agent = _FakeAgentConfig(tools={})  # empty whitelist
        tool = _FakeTool("my_tool")
        xml = _tool_call_xml("my_tool")
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
        assert "not available" in completes[0]["data"]["error"]

    async def test_tool_raises_exception(self):
        agent = _FakeAgentConfig(tools={"bad_tool": "auto"})
        tool = _FailingTool("bad_tool")
        xml = _tool_call_xml("bad_tool")
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

    async def test_tool_call_parse_error(self):
        """Malformed tool_call XML → engine emits paired TOOL_START + TOOL_COMPLETE.

        Pairing contract: every TOOL_COMPLETE must have a preceding TOOL_START
        for the same tool. The parser-error branch used to violate this by
        emitting only TOOL_COMPLETE, which forced live SSE / replay consumers
        to write orphan-tolerance code. We now require the pair so both
        consumer paths can rely on the invariant.
        """
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        xml = '<tool_call>some random garbage</tool_call>'
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, store = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": _FakeTool("my_tool")},
        )

        assert result["completed"] is True

        # Parser turns malformed input into ToolCall(name="__malformed__", error=...)
        starts = [e for e in emitted if e["type"] == "tool_start" and e["data"]["tool"] == "__malformed__"]
        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "__malformed__"]
        assert len(starts) == 1, "parser-error path must emit TOOL_START to keep the pairing contract"
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "could not be parsed" in completes[0]["data"]["error"]

        # START must precede COMPLETE in the emit stream
        start_idx = next(i for i, e in enumerate(emitted) if e["type"] == "tool_start" and e["data"]["tool"] == "__malformed__")
        complete_idx = next(i for i, e in enumerate(emitted) if e["type"] == "tool_complete" and e["data"]["tool"] == "__malformed__")
        assert start_idx < complete_idx


# ============================================================
# TestPermissionInterrupt
# ============================================================


class TestPermissionInterrupt:

    async def test_confirm_tool_emits_permission_request(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _tool_call_xml("sensitive_tool", query="test")

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

        xml = _tool_call_xml("sensitive_tool", query="test")
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

        xml = _tool_call_xml("sensitive_tool", query="test")
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

        xml = _tool_call_xml("sensitive_tool", query="test")

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

        xml = (
            _tool_call_xml("t1", param="val1")
            + "\n"
            + _tool_call_xml("t2", param="val2")
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

        with patch("models.llm.astream_with_retry", _make_fake_stream(_tool_call_chunks(xml))):
            result = await execute_loop(
                state=state,
                agents={"lead_agent": agent},
                tools={"t1": _FakeTool("t1"), "t2": _FakeTool("t2")},
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


# ============================================================
# TestRoundLimits
# ============================================================


class TestRoundLimits:

    async def test_max_tool_rounds_injects_system_message(self):
        """After max_tool_rounds, a system message should be injected."""
        agent = _FakeAgentConfig(tools={"my_tool": "auto"}, max_tool_rounds=1)
        tool = _FakeTool("my_tool")

        xml = _tool_call_xml("my_tool", query="test")

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

        # Second call should have the system message about max rounds
        if len(captured_messages) >= 2:
            last_call_msgs = captured_messages[-1]
            system_msgs = [m for m in last_call_msgs if m["role"] == "system"]
            has_limit_msg = any("maximum number of tool calls" in m["content"] for m in system_msgs)
            assert has_limit_msg


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
        xml = _tool_call_xml("my_tool", query="test")

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
        xml = _tool_call_xml("my_tool", query="test")

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
                [{"role": "user", "content": "hi"}], model="fake-model"
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
        assert summary_ev.data["model"] == "fake-model"
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

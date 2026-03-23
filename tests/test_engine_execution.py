"""
Engine execution flow tests.

Covers: agent routing, tool execution, cancellation, permission interrupts,
subagent routing, round limits, pending message drain, and metrics.

Mock strategy: patch("models.llm.astream_with_retry") + real TaskManager.
"""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from core.engine import create_initial_state, execute_loop
from core.events import StreamEventType, ExecutionEvent
from api.services.task_manager import TaskManager
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


async def _run_engine(
    llm_factory,
    agents=None,
    tools=None,
    task="hello",
    message_id="msg-1",
    conversation_history=None,
    task_manager=None,
    permission_timeout=1,
):
    """Helper to run engine with given LLM factory and return (state, emitted)."""
    state = create_initial_state(
        task=task,
        session_id="sess-1",
        message_id=message_id,
        conversation_history=conversation_history or [],
    )

    if task_manager is None:
        task_manager = TaskManager(max_concurrent=5)

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
            task_manager=task_manager,
            emit=capture_emit,
        )

    return result, emitted, task_manager


def _events_of_type(emitted, event_type):
    return [e for e in emitted if e["type"] == event_type]


# ============================================================
# TestLeadCompletion
# ============================================================


class TestLeadCompletion:

    async def test_plain_text_completes(self):
        result, emitted, tm = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("Done!"))
        )
        assert result["completed"] is True
        assert result["response"] == "Done!"
        await tm.shutdown(timeout=1)

    async def test_agent_start_and_complete_events(self):
        result, emitted, tm = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("ok"))
        )

        starts = _events_of_type(emitted, "agent_start")
        completes = _events_of_type(emitted, "agent_complete")
        assert len(starts) == 1
        assert len(completes) == 1
        assert starts[0]["agent"] == "lead_agent"
        assert completes[0]["agent"] == "lead_agent"
        await tm.shutdown(timeout=1)


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

        result, emitted, tm = await _run_engine(
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

        await tm.shutdown(timeout=1)

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

        result, emitted, tm = await _run_engine(
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
        await tm.shutdown(timeout=1)


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

        result, emitted, tm = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        starts = [e for e in emitted if e["type"] == "tool_start" and e["data"]["tool"] == "my_tool"]
        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "my_tool"]
        assert len(starts) == 1
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is True
        await tm.shutdown(timeout=1)

    async def test_tool_not_found(self):
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        xml = _tool_call_xml("my_tool")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, tm = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={},  # no tools registered
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "my_tool"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "not found" in completes[0]["data"]["error"]
        await tm.shutdown(timeout=1)

    async def test_tool_not_in_whitelist(self):
        agent = _FakeAgentConfig(tools={})  # empty whitelist
        tool = _FakeTool("my_tool")
        xml = _tool_call_xml("my_tool")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, tm = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "my_tool"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "not available" in completes[0]["data"]["error"]
        await tm.shutdown(timeout=1)

    async def test_tool_raises_exception(self):
        agent = _FakeAgentConfig(tools={"bad_tool": "auto"})
        tool = _FailingTool("bad_tool")
        xml = _tool_call_xml("bad_tool")
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("recovered"),
        ]

        result, emitted, tm = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"bad_tool": tool},
        )

        completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "bad_tool"]
        assert len(completes) == 1
        assert completes[0]["data"]["success"] is False
        assert "exploded" in completes[0]["data"]["error"]
        await tm.shutdown(timeout=1)

    async def test_tool_call_parse_error(self):
        """Malformed tool_call XML → engine should not crash."""
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        xml = '<tool_call>some random garbage</tool_call>'
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("ok"),
        ]

        result, emitted, tm = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": _FakeTool("my_tool")},
        )

        assert result["completed"] is True
        await tm.shutdown(timeout=1)


# ============================================================
# TestPermissionInterrupt
# ============================================================


class TestPermissionInterrupt:

    async def test_confirm_tool_emits_permission_request(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _tool_call_xml("sensitive_tool", query="test")

        tm = TaskManager(max_concurrent=5)
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", conversation_history=[])
        emitted = []

        async def _resolve_after_delay():
            """Wait until interrupt exists, then resolve."""
            for _ in range(100):
                if tm.get_interrupt("msg-1"):
                    await tm.resolve_interrupt("msg-1", {"approved": True})
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
                task_manager=tm,
                emit=capture_emit,
            )

        perm_requests = _events_of_type(emitted, "permission_request")
        assert len(perm_requests) == 1

        perm_results = _events_of_type(emitted, "permission_result")
        assert len(perm_results) == 1
        assert perm_results[0]["data"]["approved"] is True
        await tm.shutdown(timeout=1)

    async def test_denied_tool_not_executed(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _tool_call_xml("sensitive_tool", query="test")
        tm = TaskManager(max_concurrent=5)
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", conversation_history=[])
        emitted = []

        async def _resolve_deny():
            for _ in range(100):
                if tm.get_interrupt("msg-1"):
                    await tm.resolve_interrupt("msg-1", {"approved": False})
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
                task_manager=tm,
                emit=capture_emit,
            )

        perm_results = _events_of_type(emitted, "permission_result")
        assert perm_results[0]["data"]["approved"] is False

        # Tool complete should show error (denied)
        tool_completes = [e for e in emitted if e["type"] == "tool_complete" and e["data"]["tool"] == "sensitive_tool"]
        assert any("denied" in str(tc["data"].get("error", "")).lower() for tc in tool_completes)
        await tm.shutdown(timeout=1)

    async def test_always_allow_skips_subsequent(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _tool_call_xml("sensitive_tool", query="test")
        tm = TaskManager(max_concurrent=5)
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", conversation_history=[])
        emitted = []

        async def _resolve_allow():
            for _ in range(100):
                if tm.get_interrupt("msg-1"):
                    await tm.resolve_interrupt("msg-1", {"approved": True, "always_allow": True})
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
                task_manager=tm,
                emit=capture_emit,
            )

        # Only one permission request should have been emitted
        perm_requests = _events_of_type(emitted, "permission_request")
        assert len(perm_requests) == 1
        assert "sensitive_tool" in state["always_allowed_tools"]
        await tm.shutdown(timeout=1)

    async def test_timeout_treated_as_denied(self):
        agent = _FakeAgentConfig(tools={"sensitive_tool": "confirm"})
        tool = _FakeTool("sensitive_tool", permission=ToolPermission.CONFIRM)

        xml = _tool_call_xml("sensitive_tool", query="test")

        # Use very short timeout
        rounds = [
            _tool_call_chunks(xml),
            _simple_llm_chunks("timed out"),
        ]

        result, emitted, tm = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"sensitive_tool": tool},
            permission_timeout=0,  # immediate timeout
        )

        perm_results = _events_of_type(emitted, "permission_result")
        assert len(perm_results) == 1
        assert perm_results[0]["data"]["approved"] is False
        await tm.shutdown(timeout=1)


# ============================================================
# TestCancellation
# ============================================================


class TestCancellation:

    async def test_cancel_at_loop_top(self):
        """Cancellation flag set before LLM call → immediate exit."""
        tm = TaskManager(max_concurrent=5)
        tm._cancellations["msg-1"] = asyncio.Event()
        tm._cancellations["msg-1"].set()

        result, emitted, _ = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("should not reach")),
            task_manager=tm,
            message_id="msg-1",
        )

        assert result["completed"] is True
        assert result.get("cancelled") is True
        await tm.shutdown(timeout=1)

    async def test_cancel_between_tools(self):
        """Cancel during tool execution → break out of tool loop."""
        agent = _FakeAgentConfig(tools={"t1": "auto", "t2": "auto"})

        xml = (
            _tool_call_xml("t1", param="val1")
            + "\n"
            + _tool_call_xml("t2", param="val2")
        )

        tm = TaskManager(max_concurrent=5)
        state = create_initial_state(task="test", session_id="s1", message_id="msg-1", conversation_history=[])
        emitted = []

        async def capture_emit(event_dict):
            emitted.append(event_dict)
            # Cancel after first tool completes
            if event_dict["type"] == "tool_complete":
                tm._cancellations["msg-1"] = asyncio.Event()
                tm._cancellations["msg-1"].set()

        with patch("models.llm.astream_with_retry", _make_fake_stream(_tool_call_chunks(xml))):
            result = await execute_loop(
                state=state,
                agents={"lead_agent": agent},
                tools={"t1": _FakeTool("t1"), "t2": _FakeTool("t2")},
                task_manager=tm,
                emit=capture_emit,
            )

        assert result["completed"] is True
        assert result.get("cancelled") is True
        await tm.shutdown(timeout=1)

    async def test_cancelled_state_flags(self):
        tm = TaskManager(max_concurrent=5)
        tm._cancellations["msg-1"] = asyncio.Event()
        tm._cancellations["msg-1"].set()

        result, _, _ = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("x")),
            task_manager=tm,
            message_id="msg-1",
        )

        assert result["cancelled"] is True
        assert result["completed"] is True
        await tm.shutdown(timeout=1)


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

        result, emitted, tm = await _run_engine(
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
        await tm.shutdown(timeout=1)


# ============================================================
# TestPendingMessageDrain
# ============================================================


class TestPendingMessageDrain:

    async def test_lead_drains_before_completing(self):
        """If messages arrive during last LLM call, lead should continue instead of completing."""
        tm = TaskManager(max_concurrent=5)
        call_count = {"n": 0}

        async def injecting_stream(messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Inject a message during first LLM call
                tm.inject_message("msg-1", "injected content")
                for c in _simple_llm_chunks("first response"):
                    yield c
            else:
                for c in _simple_llm_chunks("final response"):
                    yield c

        result, emitted, _ = await _run_engine(
            injecting_stream,
            task_manager=tm,
            message_id="msg-1",
        )

        assert result["completed"] is True
        assert result["response"] == "final response"

        # Should have queued_message event
        queued = [e for e in emitted if e["type"] == StreamEventType.QUEUED_MESSAGE.value]
        assert len(queued) >= 1
        await tm.shutdown(timeout=1)


# ============================================================
# TestMetrics
# ============================================================


class TestMetrics:

    async def test_metrics_timestamps(self):
        result, _, tm = await _run_engine(
            _make_fake_stream(_simple_llm_chunks("ok"))
        )

        metrics = result["execution_metrics"]
        assert metrics["started_at"] is not None
        assert metrics["completed_at"] is not None
        assert metrics["total_duration_ms"] is not None
        assert metrics["total_duration_ms"] >= 0
        await tm.shutdown(timeout=1)

    async def test_token_usage_aggregation(self):
        """Multi-round token usage should be aggregated."""
        agent = _FakeAgentConfig(tools={"my_tool": "auto"})
        tool = _FakeTool("my_tool")
        xml = _tool_call_xml("my_tool", query="test")

        rounds = [
            _tool_call_chunks(xml, input_tokens=100, output_tokens=50),
            _simple_llm_chunks("Done", input_tokens=200, output_tokens=30),
        ]

        result, _, tm = await _run_engine(
            _make_fake_stream_sequence(rounds),
            agents={"lead_agent": agent},
            tools={"my_tool": tool},
        )

        total = result["execution_metrics"]["total_token_usage"]
        assert total["input_tokens"] == 300
        assert total["output_tokens"] == 80
        assert total["total_tokens"] == 380
        await tm.shutdown(timeout=1)

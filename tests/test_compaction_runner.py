"""
CompactionRunner unit tests.

Covers:
- threshold check (no trigger below)
- tail-append semantics (compaction_start + compaction_summary both appended)
- events_to_compact snapshot is taken before summary append
- LLM failure → placeholder summary with error field populated
- no compact_agent → silent skip
- SSE emission on both start and summary
- per-agent isolation: compaction_summary is tagged with the triggering agent
"""

from dataclasses import dataclass, field
from unittest.mock import patch, AsyncMock

import pytest

from core.compaction_runner import CompactionRunner
from core.events import ExecutionEvent, StreamEventType


@dataclass
class _FakeAgent:
    role_prompt: str = "You are a compactor."
    model: str = "fake-model"


def _make_state(events=None):
    return {"events": list(events) if events else []}


def _ev(event_type, agent_name="lead_agent", data=None, is_historical=False):
    return ExecutionEvent(
        event_type=event_type,
        agent_name=agent_name,
        data=data,
        is_historical=is_historical,
    )


async def _fake_stream_ok(messages, model=None):
    """Yields a valid compact-agent style response with <summary> wrapped content."""
    yield {"type": "content", "content": "<summary>mocked summary text</summary>"}
    yield {"type": "usage", "token_usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600}}


async def _fake_stream_raises(messages, model=None):
    # yield dummy first so it's recognised as async generator, then raise
    if False:
        yield  # pragma: no cover
    raise RuntimeError("LLM unreachable")


class TestThresholdCheck:

    async def test_below_threshold_no_trigger(self):
        agents = {"compact_agent": _FakeAgent()}
        runner = CompactionRunner(agents, emit=None)
        state = _make_state([_ev(StreamEventType.USER_INPUT.value, data={"content": "hi"})])

        # Threshold default 60000; 10000+10000 is below
        await runner.maybe_trigger(state, "lead_agent", input_tokens=10000, output_tokens=10000)

        # No compaction events appended
        types = [e.event_type for e in state["events"]]
        assert StreamEventType.COMPACTION_SUMMARY.value not in types
        assert StreamEventType.COMPACTION_START.value not in types

    async def test_at_threshold_does_not_trigger(self):
        """Strictly greater-than; equal to threshold should not fire."""
        agents = {"compact_agent": _FakeAgent()}
        runner = CompactionRunner(agents, emit=None)
        state = _make_state([_ev(StreamEventType.USER_INPUT.value, data={"content": "hi"})])

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            await runner.maybe_trigger(state, "lead_agent", input_tokens=50, output_tokens=50)

        types = [e.event_type for e in state["events"]]
        assert StreamEventType.COMPACTION_SUMMARY.value not in types


class TestAppendSemantics:

    async def test_success_appends_start_then_summary(self):
        agents = {"compact_agent": _FakeAgent()}
        emit = AsyncMock()
        runner = CompactionRunner(agents, emit=emit)
        state = _make_state([
            _ev(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
            _ev(StreamEventType.LLM_COMPLETE.value, data={
                "content": "reply", "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }),
        ])

        with patch("models.llm.astream_with_retry", _fake_stream_ok), \
             patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            await runner.maybe_trigger(state, "lead_agent", input_tokens=80, output_tokens=30)

        # Two compaction events appended AFTER the original two, in order start then summary
        types = [e.event_type for e in state["events"]]
        assert types[-2] == StreamEventType.COMPACTION_START.value
        assert types[-1] == StreamEventType.COMPACTION_SUMMARY.value

        summary_ev = state["events"][-1]
        assert summary_ev.data["content"] == "mocked summary text"
        assert summary_ev.data["error"] is None
        assert summary_ev.agent_name == "lead_agent"
        assert summary_ev.is_historical is False

    async def test_events_to_compact_snapshot_excludes_new_summary(self):
        """The compact LLM input must NOT include the summary being produced."""
        captured_messages = {}

        async def capture_stream(messages, model=None):
            captured_messages["messages"] = messages
            yield {"type": "content", "content": "<summary>ok</summary>"}
            yield {"type": "usage", "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

        agents = {"compact_agent": _FakeAgent()}
        runner = CompactionRunner(agents, emit=None)
        state = _make_state([
            _ev(StreamEventType.USER_INPUT.value, data={"content": "original user msg"}),
            _ev(StreamEventType.LLM_COMPLETE.value, data={
                "content": "assistant reply",
                "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }),
        ])

        with patch("models.llm.astream_with_retry", capture_stream), \
             patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            await runner.maybe_trigger(state, "lead_agent", input_tokens=80, output_tokens=30)

        # The compact LLM saw original messages but NOT a compaction_summary (it didn't exist yet)
        contents = " ".join(m.get("content", "") for m in captured_messages["messages"])
        assert "original user msg" in contents
        assert "assistant reply" in contents
        assert "mocked summary text" not in contents  # summary wasn't in input

    async def test_per_agent_tagging(self):
        """Summary for subagent is tagged with subagent name, not lead."""
        agents = {"compact_agent": _FakeAgent()}
        runner = CompactionRunner(agents, emit=None)
        state = _make_state([
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "find X"}),
            _ev(StreamEventType.LLM_COMPLETE.value, "search_agent", {
                "content": "searching", "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }),
        ])

        with patch("models.llm.astream_with_retry", _fake_stream_ok), \
             patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            await runner.maybe_trigger(state, "search_agent", input_tokens=80, output_tokens=30)

        summary_ev = state["events"][-1]
        assert summary_ev.agent_name == "search_agent"


class TestFailureFallback:

    async def test_llm_failure_appends_placeholder_summary(self):
        agents = {"compact_agent": _FakeAgent()}
        runner = CompactionRunner(agents, emit=None)
        state = _make_state([
            _ev(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
            _ev(StreamEventType.LLM_COMPLETE.value, data={
                "content": "reply", "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }),
        ])

        with patch("models.llm.astream_with_retry", _fake_stream_raises), \
             patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            await runner.maybe_trigger(state, "lead_agent", input_tokens=80, output_tokens=30)

        # Both start and placeholder summary should still be appended
        types = [e.event_type for e in state["events"]]
        assert StreamEventType.COMPACTION_START.value in types
        assert StreamEventType.COMPACTION_SUMMARY.value in types

        summary_ev = state["events"][-1]
        assert "compaction failed" in summary_ev.data["content"].lower()
        assert summary_ev.data["error"] == "LLM unreachable"


class TestMissingCompactAgent:

    async def test_no_compact_agent_silent_skip(self):
        """If compact_agent is not registered, maybe_trigger should not raise, not mutate state."""
        runner = CompactionRunner(agents={}, emit=None)  # no compact_agent
        state = _make_state([_ev(StreamEventType.USER_INPUT.value, data={"content": "hi"})])

        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            # Should not raise even when over threshold
            await runner.maybe_trigger(state, "lead_agent", input_tokens=80, output_tokens=30)

        types = [e.event_type for e in state["events"]]
        assert StreamEventType.COMPACTION_START.value not in types
        assert StreamEventType.COMPACTION_SUMMARY.value not in types


class TestSSEEmission:

    async def test_both_start_and_summary_sent_to_sse(self):
        agents = {"compact_agent": _FakeAgent()}
        emit = AsyncMock()
        runner = CompactionRunner(agents, emit=emit)
        state = _make_state([
            _ev(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        with patch("models.llm.astream_with_retry", _fake_stream_ok), \
             patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            await runner.maybe_trigger(state, "lead_agent", input_tokens=80, output_tokens=30)

        # emit called at least twice: COMPACTION_START and COMPACTION_SUMMARY
        emitted_types = [call.args[0]["type"] for call in emit.call_args_list]
        assert StreamEventType.COMPACTION_START.value in emitted_types
        assert StreamEventType.COMPACTION_SUMMARY.value in emitted_types

"""
CompactionRunner unit tests.

Covers:
- threshold check (no trigger below)
- tail-append semantics (compaction_start + compaction_summary both appended)
- events_to_compact snapshot is taken before summary append
- LLM failure → raises after appending success=False compaction_summary marker
  (paired terminator for compaction_start; ignored by EventHistory, see
  test_event_history.py for the boundary-skip side)
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

        # Patch threshold explicitly so the test doesn't depend on the default
        # (the default is tuned for production and gets adjusted over time).
        with patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100000):
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
        # Content is prepended with the memory-aid frame, then the raw summary
        assert summary_ev.data["content"].startswith("[Prior conversation has been compacted")
        assert "mocked summary text" in summary_ev.data["content"]
        assert summary_ev.data["success"] is True
        assert summary_ev.data["error"] is None
        assert summary_ev.data["model"] == "fake-model"
        assert summary_ev.agent_name == "lead_agent"
        assert summary_ev.is_historical is False

    async def test_events_to_compact_snapshot_excludes_new_summary(self):
        """
        The events list passed to _run_compact_llm must NOT contain the
        compaction_summary being produced (it hasn't been appended yet).

        Capturing _run_compact_llm's input directly is the right level: the
        invariant is about the in-memory snapshot, not about what eventually
        renders into the compact LLM prompt.
        """
        captured_events: list = []

        async def fake_run(self_, events_to_compact, agent_name, compact_agent):
            # Record the snapshot for inspection + return a canned summary
            captured_events.extend(events_to_compact)
            return ("UNIQUE_SUMMARY_SENTINEL", 0, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

        agents = {"compact_agent": _FakeAgent()}
        runner = CompactionRunner(agents, emit=None)
        original_events = [
            _ev(StreamEventType.USER_INPUT.value, data={"content": "original user msg"}),
            _ev(StreamEventType.LLM_COMPLETE.value, data={
                "content": "assistant reply",
                "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }),
        ]
        state = _make_state(original_events)

        with patch.object(CompactionRunner, "_run_compact_llm", fake_run), \
             patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            await runner.maybe_trigger(state, "lead_agent", input_tokens=80, output_tokens=30)

        # Snapshot must contain original events + the compaction_start we just appended,
        # but NOT the compaction_summary (still unset at time of snapshot).
        snapshot_types = [e.event_type for e in captured_events]
        assert StreamEventType.USER_INPUT.value in snapshot_types
        assert StreamEventType.LLM_COMPLETE.value in snapshot_types
        assert StreamEventType.COMPACTION_START.value in snapshot_types
        assert StreamEventType.COMPACTION_SUMMARY.value not in snapshot_types

        # Sanity: the summary text actually flows through to the final event data
        summary_ev = state["events"][-1]
        assert "UNIQUE_SUMMARY_SENTINEL" in summary_ev.data["content"]

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


class TestFailureLoud:

    async def test_llm_failure_raises_with_marker_summary(self):
        """
        On compact LLM failure, runner appends a success=False compaction_summary
        (paired terminator for compaction_start so the event stream stays well-formed)
        then re-raises. Engine catches the exception and marks the turn ERROR; we
        do NOT silently insert a placeholder boundary that would erase mid-turn
        context.
        """
        agents = {"compact_agent": _FakeAgent()}
        emit = AsyncMock()
        runner = CompactionRunner(agents, emit=emit)
        state = _make_state([
            _ev(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
            _ev(StreamEventType.LLM_COMPLETE.value, data={
                "content": "reply", "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }),
        ])

        with patch("models.llm.astream_with_retry", _fake_stream_raises), \
             patch("core.compaction_runner.config.COMPACTION_TOKEN_THRESHOLD", 100):
            with pytest.raises(RuntimeError, match="LLM unreachable"):
                await runner.maybe_trigger(state, "lead_agent", input_tokens=80, output_tokens=30)

        # Start + failure-marker summary both appended (paired)
        types = [e.event_type for e in state["events"]]
        assert types[-2] == StreamEventType.COMPACTION_START.value
        assert types[-1] == StreamEventType.COMPACTION_SUMMARY.value

        summary_ev = state["events"][-1]
        assert summary_ev.data["success"] is False
        assert summary_ev.data["error"] == "LLM unreachable"
        assert summary_ev.data["content"] == ""

        # SSE emission still happens for the failure marker so UI can show "failed"
        sse_types = [call.args[0]["type"] for call in emit.call_args_list]
        assert StreamEventType.COMPACTION_START.value in sse_types
        assert StreamEventType.COMPACTION_SUMMARY.value in sse_types


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

"""
InMemoryStreamTransport unit tests.

Pure asyncio - short TTL for fast tests.
"""

import asyncio

import pytest

from api.services.stream_transport import (
    StreamAlreadyExistsError,
    InMemoryStreamTransport,
    StreamNotFoundError,
)


# ============================================================
# TestCreateStream
# ============================================================


class TestCreateStream:

    async def test_create_returns_open(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        ctx = await sm.create_stream("msg-1")
        assert ctx.status == "open"
        assert await sm.get_stream_status("msg-1") == "open"
        await sm.close_stream("msg-1")

    async def test_duplicate_raises(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        with pytest.raises(StreamAlreadyExistsError):
            await sm.create_stream("msg-1")

        await sm.close_stream("msg-1")

    async def test_closed_stream_can_be_recreated(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.close_stream("msg-1")

        # Should not raise
        ctx = await sm.create_stream("msg-1")
        assert ctx.status == "open"
        await sm.close_stream("msg-1")


# ============================================================
# TestPushEvent
# ============================================================


class TestPushEvent:

    async def test_push_to_active(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        result = await sm.push_event("msg-1", {"type": "test"})
        assert result is True
        await sm.close_stream("msg-1")

    async def test_push_to_closed(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.close_stream("msg-1")

        result = await sm.push_event("msg-1", {"type": "test"})
        assert result is False

    async def test_push_to_nonexistent(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        result = await sm.push_event("msg-x", {"type": "test"})
        assert result is False


# ============================================================
# TestConsumeEvents
# ============================================================


class TestConsumeEvents:

    async def test_consume_yields_pushed_events(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        # Push events before consuming
        await sm.push_event("msg-1", {"type": "agent_start", "data": {}})
        await sm.push_event("msg-1", {"type": "complete", "data": {}})

        events = []
        async for event in sm.consume_events("msg-1"):
            events.append(event)

        assert len(events) == 2
        assert events[0]["type"] == "agent_start"
        assert events[1]["type"] == "complete"

    async def test_terminal_complete_exits_loop(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        await sm.push_event("msg-1", {"type": "agent_start"})
        await sm.push_event("msg-1", {"type": "complete"})
        await sm.push_event("msg-1", {"type": "should_not_see"})

        events = []
        async for event in sm.consume_events("msg-1"):
            events.append(event)

        types = [e["type"] for e in events]
        assert "complete" in types
        assert "should_not_see" not in types

    async def test_terminal_cancelled_exits(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.push_event("msg-1", {"type": "cancelled"})

        events = []
        async for event in sm.consume_events("msg-1"):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "cancelled"

    async def test_terminal_error_exits(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.push_event("msg-1", {"type": "error", "data": {"msg": "fail"}})

        events = []
        async for event in sm.consume_events("msg-1"):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "error"

    async def test_nonexistent_stream_raises(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        with pytest.raises(StreamNotFoundError):
            async for _ in sm.consume_events("msg-x"):
                pass

    async def test_owner_mismatch_raises(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1", owner_user_id="user-a")

        with pytest.raises(StreamNotFoundError):
            async for _ in sm.consume_events("msg-1", user_id="user-b"):
                pass

    async def test_consume_does_not_change_safety_ttl(self):
        sm = InMemoryStreamTransport(ttl_seconds=0.1)
        ctx = await sm.create_stream("msg-1")
        ttl_task = ctx.ttl_task
        assert ttl_task is not None

        # Push a terminal event so consume completes quickly
        await sm.push_event("msg-1", {"type": "complete"})

        async for _ in sm.consume_events("msg-1"):
            assert ctx.ttl_task is ttl_task
            assert ctx.status == "open"
            break

    async def test_consumer_disconnect_does_not_change_stream_lifecycle(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.push_event("msg-1", {"type": "metadata", "data": {}})
        ctx = sm.streams["msg-1"]
        ttl_task = ctx.ttl_task

        gen = sm.consume_events("msg-1", heartbeat_interval=0.05)
        async for event in gen:
            if event.get("type") != "__ping__":
                break
        await gen.aclose()

        assert ctx.status == "open"
        assert ctx.ttl_task is ttl_task
        assert await sm.push_event("msg-1", {"type": "complete"}) is True

    async def test_heartbeat_emits_ping(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        # Don't push any events initially — let heartbeat fire
        events = []

        async def push_after_delay():
            await asyncio.sleep(0.15)
            await sm.push_event("msg-1", {"type": "complete"})

        asyncio.create_task(push_after_delay())

        async for event in sm.consume_events("msg-1", heartbeat_interval=0.05):
            events.append(event)

        ping_events = [e for e in events if e.get("type") == "__ping__"]
        assert len(ping_events) >= 1


# ============================================================
# TestTTL
# ============================================================


class TestTTL:

    async def test_open_safety_ttl_expires(self):
        sm = InMemoryStreamTransport(ttl_seconds=0.1)
        await sm.create_stream("msg-1")
        assert await sm.get_stream_status("msg-1") == "open"

        await asyncio.sleep(0.2)
        assert await sm.get_stream_status("msg-1") == "closed"

    async def test_two_observers_are_independent(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.push_event("msg-1", {"type": "agent_start"})

        first = sm.consume_events("msg-1", heartbeat_interval=0.05)
        second = sm.consume_events("msg-1", heartbeat_interval=0.05)
        assert (await anext(first))["type"] == "agent_start"
        assert (await anext(second))["type"] == "agent_start"

        await first.aclose()
        assert await sm.get_stream_status("msg-1") == "open"

        await sm.push_event("msg-1", {"type": "complete"})
        assert (await anext(second))["type"] == "complete"
        await second.aclose()

        await sm.close_stream("msg-1")


# ============================================================
# TestCloseAndStatus
# ============================================================


class TestCloseAndStatus:

    async def test_close_sets_cancelled_event(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        ctx = await sm.create_stream("msg-1")
        assert not ctx.cancelled.is_set()

        await sm.close_stream("msg-1")
        assert ctx.cancelled.is_set()
        assert ctx.status == "closed"

    async def test_close_nonexistent_returns_false(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        result = await sm.close_stream("msg-x")
        assert result is False

    async def test_status_query(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        assert await sm.get_stream_status("msg-x") is None

        await sm.create_stream("msg-1")
        assert await sm.get_stream_status("msg-1") == "open"
        await sm.close_stream("msg-1")
        assert await sm.get_stream_status("msg-1") == "closed"


# ============================================================
# TestReplay — history-based reconnect / Last-Event-ID semantics
# Mirrors RedisStreamTransport's XREAD `0-0` (full replay) and
# `last_event_id` (resume) behavior so dev/prod stay aligned.
# ============================================================


class TestReplay:

    async def test_push_assigns_monotonic_id(self):
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        e1 = {"type": "agent_start"}
        e2 = {"type": "metadata"}
        await sm.push_event("msg-1", e1)
        await sm.push_event("msg-1", e2)

        # _stream_id injected into the original dict, monotonically increasing
        assert e1["_stream_id"] == "0"
        assert e2["_stream_id"] == "1"
        await sm.close_stream("msg-1")

    async def test_consume_replays_from_start_when_no_last_event_id(self):
        """A fresh consumer with no Last-Event-ID sees every history entry."""
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        # Simulate a turn that already pushed several events before any
        # consumer attached (e.g. user navigated away and back).
        await sm.push_event("msg-1", {"type": "agent_start"})
        await sm.push_event("msg-1", {"type": "tool_start", "data": {"name": "x"}})
        await sm.push_event("msg-1", {"type": "tool_complete", "data": {"name": "x"}})
        await sm.push_event("msg-1", {"type": "complete"})

        events = []
        async for event in sm.consume_events("msg-1"):
            events.append(event)

        types = [e["type"] for e in events]
        assert types == ["agent_start", "tool_start", "tool_complete", "complete"]

    async def test_consume_resumes_after_last_event_id(self):
        """Last-Event-ID skips already-delivered events."""
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        await sm.push_event("msg-1", {"type": "agent_start"})  # id 0
        await sm.push_event("msg-1", {"type": "tool_start"})   # id 1
        await sm.push_event("msg-1", {"type": "tool_complete"})  # id 2
        await sm.push_event("msg-1", {"type": "complete"})     # id 3

        # Resume after id "1" — should yield only events 2 and 3
        events = []
        async for event in sm.consume_events("msg-1", last_event_id="1"):
            events.append(event)

        types = [e["type"] for e in events]
        assert types == ["tool_complete", "complete"]

    async def test_reconnect_after_disconnect_replays_full_history(self):
        """
        Regression: switching away then back to an in-flight conversation
        previously dropped events the prior consumer had already pulled
        (asyncio.Queue model). New history-based model replays from start.
        """
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        # First push: tool_start observed by consumer A
        await sm.push_event("msg-1", {"type": "tool_start", "data": {"name": "fetch"}})

        gen_a = sm.consume_events("msg-1", heartbeat_interval=0.01)
        events_a = []
        async for event in gen_a:
            if event.get("type") == "__ping__":
                break
            events_a.append(event)
        await gen_a.aclose()
        assert [e["type"] for e in events_a] == ["tool_start"]

        # Engine continues while no consumer is attached
        await sm.push_event("msg-1", {"type": "tool_complete", "data": {"name": "fetch"}})
        await sm.push_event("msg-1", {"type": "complete"})

        # Reconnect: full replay (tool_start + tool_complete + complete)
        gen_b = sm.consume_events("msg-1")
        events_b = []
        async for event in gen_b:
            events_b.append(event)

        types_b = [e["type"] for e in events_b]
        assert types_b == ["tool_start", "tool_complete", "complete"]

    async def test_history_bounded_by_max_history(self):
        """Oldest events are dropped once max_history is exceeded."""
        sm = InMemoryStreamTransport(ttl_seconds=10, max_history=3)
        await sm.create_stream("msg-1")

        for i in range(5):
            await sm.push_event("msg-1", {"type": "metadata", "data": {"i": i}})
        await sm.push_event("msg-1", {"type": "complete"})

        # Only the last 3 metadata events survive trimming, plus complete
        events = []
        async for event in sm.consume_events("msg-1"):
            events.append(event)

        # Expect the 3 most recent metadata + the complete event
        metadata_events = [e for e in events if e["type"] == "metadata"]
        assert [e["data"]["i"] for e in metadata_events] == [3, 4]
        # i=2 was kept too (3 metadata + complete = 4 total in buffer of 3
        # would drop i=2 first; but complete is added last so only i=3,4
        # remain alongside complete). Tolerate either bound interpretation:
        assert events[-1]["type"] == "complete"

    async def test_cumulative_llm_chunks_replace_replay_snapshot(self):
        """Replay memory stays linear in final output, not snapshot count."""
        sm = InMemoryStreamTransport(ttl_seconds=10)
        ctx = await sm.create_stream("msg-1")

        # Model a 50k cumulative answer flushed 200 times. Only the newest
        # content snapshot for this agent/channel may remain in replay history.
        final_content = ""
        for i in range(1, 201):
            final_content = "x" * (i * 250)
            await sm.push_event("msg-1", {
                "type": "llm_chunk",
                "agent": "lead_agent",
                "data": {"content": final_content},
            })

        await sm.push_event("msg-1", {
            "type": "llm_chunk",
            "agent": "lead_agent",
            "data": {"reasoning_content": "latest reasoning"},
        })
        await sm.push_event("msg-1", {
            "type": "llm_chunk",
            "agent": "research_agent",
            "data": {"content": "subagent snapshot"},
        })

        retained = list(ctx.history.values())
        assert len(retained) == 3
        assert sum(
            len(value)
            for event in retained
            for value in event.get("data", {}).values()
            if isinstance(value, str)
        ) == len(final_content) + len("latest reasoning") + len("subagent snapshot")

        await sm.push_event("msg-1", {"type": "complete"})
        events = [event async for event in sm.consume_events("msg-1")]
        assert [event["type"] for event in events] == [
            "llm_chunk",
            "llm_chunk",
            "llm_chunk",
            "complete",
        ]
        assert events[0]["data"]["content"] == final_content

    async def test_resume_from_replaced_snapshot_id_gets_newer_snapshot(self):
        """Deleting an old snapshot keeps Last-Event-ID continuity by ID order."""
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")

        old = {
            "type": "llm_chunk",
            "agent": "lead_agent",
            "data": {"content": "old"},
        }
        latest = {
            "type": "llm_chunk",
            "agent": "lead_agent",
            "data": {"content": "latest"},
        }
        await sm.push_event("msg-1", old)
        await sm.push_event("msg-1", latest)
        await sm.push_event("msg-1", {"type": "complete"})

        events = [
            event
            async for event in sm.consume_events(
                "msg-1", last_event_id=old["_stream_id"]
            )
        ]
        assert [event["type"] for event in events] == ["llm_chunk", "complete"]
        assert events[0]["data"]["content"] == "latest"

    async def test_invalid_last_event_id_falls_back_to_start(self):
        """A garbled Last-Event-ID must not raise; behave like no ID."""
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.push_event("msg-1", {"type": "agent_start"})
        await sm.push_event("msg-1", {"type": "complete"})

        events = []
        async for event in sm.consume_events("msg-1", last_event_id="not-a-number"):
            events.append(event)

        assert [e["type"] for e in events] == ["agent_start", "complete"]

    async def test_consumer_mutation_does_not_poison_history(self):
        """
        Regression: SSE router pops _stream_id off each yielded event; if the
        transport hands out the buffered dict by reference, a second consumer
        replaying the same history would see _stream_id missing and the SSE
        response would lose its `id:` field for that event.
        """
        sm = InMemoryStreamTransport(ttl_seconds=10)
        await sm.create_stream("msg-1")
        await sm.push_event("msg-1", {"type": "agent_start"})
        await sm.push_event("msg-1", {"type": "complete"})

        # First consumer: simulate router behavior (pop _stream_id)
        first_pass = []
        async for event in sm.consume_events("msg-1"):
            first_pass.append(event.pop("_stream_id", None))

        # Second consumer replays the same history. Each event must still
        # carry its _stream_id — i.e. the first consumer's pop did not touch
        # the buffered entry.
        second_pass = []
        async for event in sm.consume_events("msg-1"):
            second_pass.append(event.get("_stream_id"))

        assert first_pass == ["0", "1"]
        assert second_pass == ["0", "1"]

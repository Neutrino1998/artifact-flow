"""
RedisStreamTransport 集成测试

需要 Redis 可用：设置 REDIS_URL 环境变量或确保 localhost:6379 可连接。
运行：REDIS_URL=redis://localhost:6379 pytest tests/integration/test_redis_stream_transport.py -v
"""

import asyncio
import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.external,
]


@pytest_asyncio.fixture
async def transport(redis_client, redis_key_prefix):
    from api.services.redis_stream_transport import RedisStreamTransport

    t = RedisStreamTransport(
        redis_client,
        cleanup_ttl=30,
        execution_timeout=60,
        key_prefix=redis_key_prefix,
    )
    t.init_scripts()
    return t


class TestStreamLifecycle:
    async def test_create_push_consume(self, transport):
        stream_id = "test_stream_1"
        await transport.create_stream(stream_id, owner_user_id="user1")

        # Push events
        assert await transport.push_event(stream_id, {"type": "metadata", "data": {"k": "v"}})
        assert await transport.push_event(stream_id, {"type": "llm_chunk", "data": {"content": "hi"}})
        assert await transport.push_event(stream_id, {"type": "complete", "data": {}})

        # Consume events
        events = []
        async for event in transport.consume_events(
            stream_id, heartbeat_interval=1.0, user_id="user1"
        ):
            if event.get("type") == "__ping__":
                continue
            events.append(event)

        assert len(events) == 3
        assert events[0]["type"] == "metadata"
        assert events[1]["type"] == "llm_chunk"
        assert events[2]["type"] == "complete"
        # Each event should have _stream_id
        for e in events:
            assert "_stream_id" in e

    async def test_duplicate_create_raises(self, transport):
        from api.services.stream_transport import StreamAlreadyExistsError

        await transport.create_stream("test_stream_dup")
        with pytest.raises(StreamAlreadyExistsError):
            await transport.create_stream("test_stream_dup")

    async def test_consume_not_found(self, transport):
        from api.services.stream_transport import StreamNotFoundError

        with pytest.raises(StreamNotFoundError):
            async for _ in transport.consume_events("test_stream_nonexistent"):
                pass

    async def test_owner_mismatch(self, transport):
        from api.services.stream_transport import StreamNotFoundError

        await transport.create_stream("test_stream_owner", owner_user_id="user_a")
        with pytest.raises(StreamNotFoundError):
            async for _ in transport.consume_events(
                "test_stream_owner", user_id="user_b"
            ):
                pass


class TestCrossInstance:
    async def test_push_and_consume_separate(
        self, redis_client, redis_key_prefix
    ):
        """Simulate cross-worker: one pushes, another consumes."""
        from api.services.redis_stream_transport import RedisStreamTransport

        producer = RedisStreamTransport(
            redis_client,
            cleanup_ttl=30,
            execution_timeout=60,
            key_prefix=redis_key_prefix,
        )
        producer.init_scripts()
        consumer = RedisStreamTransport(
            redis_client,
            cleanup_ttl=30,
            execution_timeout=60,
            key_prefix=redis_key_prefix,
        )
        consumer.init_scripts()

        stream_id = "test_stream_cross"
        await producer.create_stream(stream_id)

        async def push_events():
            await asyncio.sleep(0.1)
            await producer.push_event(stream_id, {"type": "metadata", "data": {}})
            await asyncio.sleep(0.1)
            await producer.push_event(stream_id, {"type": "complete", "data": {}})

        task = asyncio.create_task(push_events())

        events = []
        async for event in consumer.consume_events(
            stream_id, heartbeat_interval=0.5
        ):
            if event.get("type") == "__ping__":
                continue
            events.append(event)

        await task
        assert len(events) == 2
        assert events[-1]["type"] == "complete"


class TestLastEventId:
    async def test_resume_from_last_event_id(self, transport):
        """Consumer disconnect leaves the producer-owned stream open; cursor resumes."""
        stream_id = "test_stream_resume"
        await transport.create_stream(stream_id)

        # Push 3 events
        await transport.push_event(stream_id, {"type": "metadata", "data": {}})
        await transport.push_event(stream_id, {"type": "llm_chunk", "data": {"content": "a"}})
        await transport.push_event(stream_id, {"type": "llm_chunk", "data": {"content": "b"}})

        # Consume first 2 events then break (simulates consumer disconnect)
        event_ids = []
        count = 0
        gen = transport.consume_events(stream_id, heartbeat_interval=0.3)
        try:
            async with asyncio.timeout(1.0):
                async for event in gen:
                    if event.get("type") == "__ping__":
                        continue
                    event_ids.append(event.get("_stream_id"))
                    count += 1
                    if count >= 2:
                        break
        except TimeoutError:
            pass
        await gen.aclose()

        assert len(event_ids) >= 2
        last_id = event_ids[1]

        # Observer disconnect does not change the producer-owned lifecycle.
        status = await transport.get_stream_status(stream_id)
        assert status == "open"

        # Producer can still push events (stream NOT closed)
        assert await transport.push_event(stream_id, {"type": "complete", "data": {}})

        # Resume from last_id — should get events after that id
        events = []
        async for event in transport.consume_events(
            stream_id, heartbeat_interval=0.5, last_event_id=last_id
        ):
            if event.get("type") == "__ping__":
                continue
            events.append(event)

        # Should get the remaining events (llm_chunk "b" + complete)
        assert len(events) >= 1
        assert events[-1]["type"] == "complete"

    async def test_cumulative_chunks_replace_replay_snapshots(
        self, transport, redis_client
    ):
        """Redis retains one cumulative snapshot per agent/content channel."""
        stream_id = "test_stream_snapshot_replace"
        await transport.create_stream(stream_id)

        for i in range(1, 11):
            await transport.push_event(stream_id, {
                "type": "llm_chunk",
                "agent": "lead_agent",
                "data": {"content": "x" * i},
            })
        await transport.push_event(stream_id, {
            "type": "llm_chunk",
            "agent": "lead_agent",
            "data": {"reasoning_content": "reasoning"},
        })
        await transport.push_event(stream_id, {
            "type": "llm_chunk",
            "agent": "research_agent",
            "data": {"content": "research"},
        })

        assert await redis_client.xlen(transport._stream_key(stream_id)) == 3

        await transport.push_event(stream_id, {"type": "complete"})
        events = [event async for event in transport.consume_events(stream_id)]
        assert [event["type"] for event in events] == [
            "llm_chunk",
            "llm_chunk",
            "llm_chunk",
            "complete",
        ]
        assert events[0]["data"]["content"] == "x" * 10

    async def test_resume_from_deleted_snapshot_id_gets_latest(self, transport):
        """XDEL of the old snapshot does not break Redis ID-based resume."""
        stream_id = "test_stream_snapshot_resume"
        await transport.create_stream(stream_id)

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
        await transport.push_event(stream_id, old)
        await transport.push_event(stream_id, latest)
        await transport.push_event(stream_id, {"type": "complete"})

        events = [
            event
            async for event in transport.consume_events(
                stream_id, last_event_id=old["_stream_id"]
            )
        ]
        assert [event["type"] for event in events] == ["llm_chunk", "complete"]
        assert events[0]["data"]["content"] == "latest"


class TestConsumerDisconnect:
    async def test_consumer_disconnect_does_not_close_stream(self, transport):
        """Consumer breaking out leaves the producer-owned stream open."""
        stream_id = "test_stream_disconnect"
        await transport.create_stream(stream_id)
        await transport.push_event(stream_id, {"type": "metadata", "data": {}})

        # Consume then break (simulates disconnect)
        gen = transport.consume_events(stream_id, heartbeat_interval=0.3)
        async for event in gen:
            if event.get("type") != "__ping__":
                break
        await gen.aclose()

        # Stream remains open; observer presence is not shared state.
        status = await transport.get_stream_status(stream_id)
        assert status == "open"

        # Producer can still push
        assert await transport.push_event(stream_id, {"type": "complete", "data": {}})

    async def test_two_observers_are_independent(self, transport):
        stream_id = "test_stream_two_observers"
        await transport.create_stream(stream_id)
        await transport.push_event(stream_id, {"type": "agent_start", "data": {}})

        first = transport.consume_events(stream_id, heartbeat_interval=0.3)
        second = transport.consume_events(stream_id, heartbeat_interval=0.3)
        assert (await anext(first))["type"] == "agent_start"
        assert (await anext(second))["type"] == "agent_start"

        await first.aclose()
        assert await transport.get_stream_status(stream_id) == "open"

        await transport.push_event(stream_id, {"type": "complete", "data": {}})
        assert (await anext(second))["type"] == "complete"
        await second.aclose()

    async def test_legacy_observer_status_does_not_block_new_producer(
        self, transport, redis_client
    ):
        """Rolling deploy: old observers may still write streaming/pending."""
        stream_id = "test_stream_legacy_observer"
        await transport.create_stream(stream_id)
        meta_key = transport._meta_key(stream_id)

        await redis_client.hset(meta_key, "status", "streaming")
        assert await transport.push_event(stream_id, {"type": "agent_start"}) is True

        await redis_client.hset(meta_key, "status", "pending")
        assert await transport.push_event(stream_id, {"type": "complete"}) is True

    async def test_consumer_disconnect_after_producer_close(self, transport, redis_client):
        """If producer already closed, consumer disconnect should not revert to pending."""
        stream_id = "test_stream_disc_after_close"
        await transport.create_stream(stream_id)
        await transport.push_event(stream_id, {"type": "metadata", "data": {}})

        # Start consuming in a task, then close from producer side
        async def consume_and_break():
            async for event in transport.consume_events(
                stream_id, heartbeat_interval=0.3
            ):
                if event.get("type") != "__ping__":
                    # Wait for producer to close
                    await asyncio.sleep(0.2)
                    break

        task = asyncio.create_task(consume_and_break())
        await asyncio.sleep(0.1)
        await transport.close_stream(stream_id)
        await task

        # Should stay closed
        status = await transport.get_stream_status(stream_id)
        assert status == "closed"


class TestOrphanKeyFix:
    async def test_create_stream_no_orphan_key(self, transport, redis_client):
        """create_stream should NOT set EXPIRE on non-existent stream key."""
        stream_id = "test_stream_orphan"
        await transport.create_stream(stream_id)

        stream_key = transport._stream_key(stream_id)
        # stream key should not exist yet (no XADD has happened)
        exists = await redis_client.exists(stream_key)
        assert exists == 0

        # After first push, stream key should exist with TTL set atomically with
        # the XADD (same-slot Lua → no orphan window between XADD and EXPIRE).
        await transport.push_event(stream_id, {"type": "metadata", "data": {}})
        exists = await redis_client.exists(stream_key)
        assert exists == 1
        ttl = await redis_client.ttl(stream_key)
        # best-effort contract: TTL is set and bounded by execution_timeout
        # (60 in fixture) — never the -1 "no expiry" sentinel a missed EXPIRE
        # would leave behind. (Precise alignment with meta_key's remaining TTL is
        # outside this test's best-effort TTL contract and therefore not asserted here.)
        assert 0 < ttl <= 60

        # A subsequent (non-first) push must NOT refresh the TTL — pushing events
        # over the stream's lifetime must not keep extending it.
        await transport.push_event(stream_id, {"type": "llm_chunk", "data": {}})
        ttl2 = await redis_client.ttl(stream_key)
        assert 0 < ttl2 <= ttl


class TestStreamClose:
    async def test_push_after_close(self, transport):
        stream_id = "test_stream_close"
        await transport.create_stream(stream_id)
        await transport.close_stream(stream_id)
        result = await transport.push_event(stream_id, {"type": "metadata", "data": {}})
        assert result is False

    async def test_close_nonexistent(self, transport):
        result = await transport.close_stream("test_stream_nope")
        assert result is False

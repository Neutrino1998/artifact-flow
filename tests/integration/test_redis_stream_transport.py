"""
RedisStreamTransport 集成测试

需要 Redis 可用：设置 REDIS_URL 环境变量或确保 localhost:6379 可连接。
运行：REDIS_URL=redis://localhost:6379 pytest tests/integration/test_redis_stream_transport.py -v
"""

import asyncio
import os

import pytest
import pytest_asyncio

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

try:
    import redis.asyncio as aioredis
    _redis_available = True
except ImportError:
    _redis_available = False

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _redis_available, reason="redis package not installed"),
]


async def _check_redis() -> bool:
    try:
        client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def redis_client():
    if not await _check_redis():
        pytest.skip("Redis not available")
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield client
    # Cleanup test keys (hash-tagged format: stream:{id} / stream_meta:{id})
    keys = await client.keys("stream:{test_*}")
    keys += await client.keys("stream_meta:{test_*}")
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest_asyncio.fixture
async def transport(redis_client):
    from api.services.redis_stream_transport import RedisStreamTransport

    t = RedisStreamTransport(
        redis_client,
        stream_ttl=30,
        stream_timeout=60,
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
    async def test_push_and_consume_separate(self, redis_client):
        """Simulate cross-worker: one pushes, another consumes."""
        from api.services.redis_stream_transport import RedisStreamTransport

        producer = RedisStreamTransport(redis_client, stream_ttl=30, stream_timeout=60)
        consumer = RedisStreamTransport(redis_client, stream_ttl=30, stream_timeout=60)

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
        """Consumer disconnect reverts to pending; reconnect with last_event_id resumes."""
        stream_id = "test_stream_resume"
        await transport.create_stream(stream_id)

        # Push 3 events
        await transport.push_event(stream_id, {"type": "metadata", "data": {}})
        await transport.push_event(stream_id, {"type": "llm_chunk", "data": {"content": "a"}})
        await transport.push_event(stream_id, {"type": "llm_chunk", "data": {"content": "b"}})

        # Consume first 2 events then break (simulates consumer disconnect)
        event_ids = []
        count = 0
        try:
            async with asyncio.timeout(1.0):
                async for event in transport.consume_events(
                    stream_id, heartbeat_interval=0.3
                ):
                    if event.get("type") == "__ping__":
                        continue
                    event_ids.append(event.get("_stream_id"))
                    count += 1
                    if count >= 2:
                        break
        except TimeoutError:
            pass

        assert len(event_ids) >= 2
        last_id = event_ids[1]

        # After consumer disconnect, stream should revert to pending (not closed)
        status = await transport.get_stream_status_async(stream_id)
        assert status == "pending"

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


class TestConsumerDisconnect:
    async def test_consumer_disconnect_does_not_close_stream(self, transport):
        """Consumer breaking out of consume_events should revert to pending, not close."""
        stream_id = "test_stream_disconnect"
        await transport.create_stream(stream_id)
        await transport.push_event(stream_id, {"type": "metadata", "data": {}})

        # Consume then break (simulates disconnect)
        async for event in transport.consume_events(
            stream_id, heartbeat_interval=0.3
        ):
            if event.get("type") != "__ping__":
                break

        # Stream should be pending, not closed
        status = await transport.get_stream_status_async(stream_id)
        assert status == "pending"

        # Producer can still push
        assert await transport.push_event(stream_id, {"type": "complete", "data": {}})

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
        status = await transport.get_stream_status_async(stream_id)
        assert status == "closed"


class TestOrphanKeyFix:
    async def test_create_stream_no_orphan_key(self, transport, redis_client):
        """create_stream should NOT set EXPIRE on non-existent stream key."""
        stream_id = "test_stream_orphan"
        await transport.create_stream(stream_id)

        stream_key = f"stream:{{{stream_id}}}"
        # stream key should not exist yet (no XADD has happened)
        exists = await redis_client.exists(stream_key)
        assert exists == 0

        # After first push, stream key should exist with TTL
        await transport.push_event(stream_id, {"type": "metadata", "data": {}})
        exists = await redis_client.exists(stream_key)
        assert exists == 1
        ttl = await redis_client.ttl(stream_key)
        assert ttl > 0  # TTL should be set


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

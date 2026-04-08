"""
RedisRuntimeStore 集成测试

需要 Redis 可用：设置 REDIS_URL 环境变量或确保 localhost:6379 可连接。
运行：REDIS_URL=redis://localhost:6379 pytest tests/integration/test_redis_runtime_store.py -v
"""

import asyncio
import os

import pytest
import pytest_asyncio

# 检查 Redis 可用性
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
    """Check if Redis is reachable."""
    try:
        client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


TEST_PREFIX = "test"


@pytest_asyncio.fixture
async def redis_client():
    """Provide a Redis client, skip if not available."""
    if not await _check_redis():
        pytest.skip("Redis not available")
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield client
    # Cleanup test keys (match {test:...}:* pattern)
    keys = await client.keys(f"{{{TEST_PREFIX}:*}}:*")
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest_asyncio.fixture
async def store(redis_client):
    """Provide a RedisRuntimeStore instance."""
    from api.services.redis_runtime_store import RedisRuntimeStore

    s = RedisRuntimeStore(
        redis_client,
        lease_ttl=10,
        execution_timeout=60,
        permission_timeout=30,
        key_prefix=TEST_PREFIX,
    )
    s.init_scripts()
    return s


class TestLease:
    async def test_acquire_and_release(self, store):
        result = await store.try_acquire_lease("test_conv_1", "test_msg_1")
        assert result is None  # acquired

        result = await store.try_acquire_lease("test_conv_1", "test_msg_2")
        assert result == "test_msg_1"  # blocked

        await store.release_lease("test_conv_1", "test_msg_1")
        result = await store.try_acquire_lease("test_conv_1", "test_msg_2")
        assert result is None  # now acquired

    async def test_release_wrong_owner(self, store):
        await store.try_acquire_lease("test_conv_2", "test_msg_a")
        # Wrong owner release should be a no-op
        await store.release_lease("test_conv_2", "test_msg_wrong")
        msg = await store.get_leased_message_id("test_conv_2")
        assert msg == "test_msg_a"

    async def test_ttl_expiry(self, store, redis_client):
        """Lease should expire after TTL."""
        await store.try_acquire_lease("test_conv_3", "test_msg_ttl")
        # Set a very short TTL for testing
        await redis_client.expire(store._lease_key("test_conv_3"), 1)
        await asyncio.sleep(1.5)
        msg = await store.get_leased_message_id("test_conv_3")
        assert msg is None

    async def test_renew_lease(self, store, redis_client):
        await store.try_acquire_lease("test_conv_4", "test_msg_renew")
        await store.mark_engine_interactive("test_conv_4", "test_msg_renew")
        # Set short TTL
        await redis_client.expire(store._lease_key("test_conv_4"), 2)
        # Renew with longer TTL
        await store.renew_lease("test_conv_4", "test_msg_renew", ttl=10)
        ttl = await redis_client.ttl(store._lease_key("test_conv_4"))
        assert ttl > 2


class TestLeaseAtomicity:
    async def test_concurrent_acquire_no_orphan(self, store):
        """Concurrent lease acquisitions should be atomic — exactly one wins."""
        conv_id = "test_conv_race"
        results = await asyncio.gather(
            store.try_acquire_lease(conv_id, "msg_a"),
            store.try_acquire_lease(conv_id, "msg_b"),
            store.try_acquire_lease(conv_id, "msg_c"),
        )
        # Exactly one should have acquired (None), the rest should get the winner's msg_id
        winners = [r for r in results if r is None]
        losers = [r for r in results if r is not None]
        assert len(winners) == 1
        assert len(losers) == 2
        # All losers should report the same existing holder
        assert len(set(losers)) == 1
        # The holder should be one of the contenders
        assert losers[0] in ("msg_a", "msg_b", "msg_c")


class TestInterrupt:
    async def test_resolve_before_timeout(self, store):
        async def resolver():
            await asyncio.sleep(0.2)
            result = await store.resolve_interrupt("test_msg_int1", {"approved": True})
            assert result == "resolved"

        task = asyncio.create_task(resolver())
        resume_data = await store.wait_for_interrupt(
            "test_msg_int1", {"tool": "dangerous"}, timeout=5.0
        )
        await task
        assert resume_data == {"approved": True}

    async def test_timeout(self, store):
        resume_data = await store.wait_for_interrupt(
            "test_msg_int2", {"tool": "test"}, timeout=0.5
        )
        assert resume_data is None

    async def test_resolve_not_found(self, store):
        result = await store.resolve_interrupt("test_msg_nonexistent", {"approved": True})
        assert result == "not_found"

    async def test_resolve_already_resolved(self, store):
        async def resolver():
            await asyncio.sleep(0.1)
            await store.resolve_interrupt("test_msg_int3", {"approved": True})

        task = asyncio.create_task(resolver())
        await store.wait_for_interrupt("test_msg_int3", {"tool": "t"}, timeout=5.0)
        await task

        result = await store.resolve_interrupt("test_msg_int3", {"approved": False})
        assert result == "already_resolved"

    async def test_get_interrupt_data(self, store):
        async def resolver():
            await asyncio.sleep(0.1)
            data = await store.get_interrupt_data("test_msg_int4")
            assert data == {"tool": "read_file"}
            await store.resolve_interrupt("test_msg_int4", {"approved": True})

        task = asyncio.create_task(resolver())
        await store.wait_for_interrupt(
            "test_msg_int4", {"tool": "read_file"}, timeout=5.0
        )
        await task


class TestCancel:
    async def test_cancel_flag(self, store):
        assert not await store.is_cancelled("test_msg_c1")
        await store.request_cancel("test_msg_c1")
        assert await store.is_cancelled("test_msg_c1")

    async def test_cancel_wakes_interrupt(self, store):
        async def canceller():
            await asyncio.sleep(0.2)
            await store.request_cancel("test_msg_c2")

        task = asyncio.create_task(canceller())
        resume_data = await store.wait_for_interrupt(
            "test_msg_c2", {"tool": "t"}, timeout=5.0
        )
        await task
        assert resume_data is not None
        assert resume_data.get("approved") is False
        assert resume_data.get("reason") == "cancelled"


class TestMessageQueue:
    async def test_inject_and_drain(self, store):
        await store.inject_message("test_msg_q1", "hello")
        await store.inject_message("test_msg_q1", "world")
        messages = await store.drain_messages("test_msg_q1")
        assert messages == ["hello", "world"]
        # Second drain should be empty
        messages = await store.drain_messages("test_msg_q1")
        assert messages == []

    async def test_drain_empty(self, store):
        messages = await store.drain_messages("test_msg_q_none")
        assert messages == []


class TestCleanup:
    async def test_cleanup_execution(self, store):
        await store.try_acquire_lease("test_conv_clean", "test_msg_clean")
        await store.mark_engine_interactive("test_conv_clean", "test_msg_clean")
        await store.inject_message("test_msg_clean", "data")

        await store.cleanup_execution("test_conv_clean", "test_msg_clean")

        assert await store.get_leased_message_id("test_conv_clean") is None
        assert await store.get_interactive_message_id("test_conv_clean") is None
        assert await store.drain_messages("test_msg_clean") == []

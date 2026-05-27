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

    async def test_list_active_executions_pairs_conv_with_msg(self, store):
        await store.try_acquire_lease("test_conv_listA", "msg-A")
        await store.try_acquire_lease("test_conv_listB", "msg-B")
        active = await store.list_active_executions()
        assert active.get("test_conv_listA") == "msg-A"
        assert active.get("test_conv_listB") == "msg-B"

        await store.release_lease("test_conv_listA", "msg-A")
        active = await store.list_active_executions()
        assert "test_conv_listA" not in active
        assert active.get("test_conv_listB") == "msg-B"
        await store.release_lease("test_conv_listB", "msg-B")

    async def test_list_active_executions_is_cluster_safe_no_mget(self, store, monkeypatch):
        """Cluster-safety pin: must NOT issue a single cross-entity MGET.

        Lease keys are hash-tagged by conv_id → distinct conversations land on
        distinct Cluster slots, so a single MGET would raise CROSSSLOT. The impl
        must fan out via pipelined per-key GET instead. A single-node test Redis
        can't reproduce CROSSSLOT, so we trip a tripwire on .mget: a regression to
        MGET fails loudly here regardless of deployment form.
        """
        async def _boom(*args, **kwargs):
            raise AssertionError(
                "list_active_executions must not call MGET (cross-slot on Cluster)"
            )
        monkeypatch.setattr(store._redis, "mget", _boom)

        await store.try_acquire_lease("test_conv_csA", "msg-cs-A")
        await store.try_acquire_lease("test_conv_csB", "msg-cs-B")
        active = await store.list_active_executions()
        assert active.get("test_conv_csA") == "msg-cs-A"
        assert active.get("test_conv_csB") == "msg-cs-B"
        await store.release_lease("test_conv_csA", "msg-cs-A")
        await store.release_lease("test_conv_csB", "msg-cs-B")

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

    async def test_mark_interactive_only_when_lease_owner(self, store):
        """mark_engine_interactive is a CAS against the lease owner (QUEUED→RUNNING).

        A stale queued task that lost its lease must not overwrite the new owner's
        interactive key — mark returns False and leaves interactive untouched.
        """
        conv = "test_conv_mark_owner"
        await store.try_acquire_lease(conv, "owner-A")

        # A different (stale) msg id must NOT be able to mark interactive.
        assert await store.mark_engine_interactive(conv, "stale-B") is False
        assert await store.get_interactive_message_id(conv) is None

        # The real owner can.
        assert await store.mark_engine_interactive(conv, "owner-A") is True
        assert await store.get_interactive_message_id(conv) == "owner-A"

        # After a takeover, the new owner marks interactive; the stale task must
        # not be able to clobber it.
        await store.release_lease(conv, "owner-A")
        await store.try_acquire_lease(conv, "owner-C")
        assert await store.mark_engine_interactive(conv, "owner-C") is True
        assert await store.get_interactive_message_id(conv) == "owner-C"
        assert await store.mark_engine_interactive(conv, "owner-A") is False
        assert await store.get_interactive_message_id(conv) == "owner-C"  # stale mark didn't clobber


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

    async def test_renew_lease_does_not_touch_cancel_flag(self, store, redis_client):
        """Cancel only targets RUNNING turns, so renew_lease must NOT manage the
        cancel flag (no renewal coupling). A pending flag keeps its own TTL; renew
        neither extends nor resurrects it."""
        conv, msg = "test_conv_cancel_norenew", "test_msg_cancel_norenew"
        await store.try_acquire_lease(conv, msg)
        await store.request_cancel(msg)
        await redis_client.expire(store._cancel_key(msg), 5)
        await store.renew_lease(conv, msg, ttl=store._lease_ttl)
        ttl = await redis_client.ttl(store._cancel_key(msg))
        assert 0 < ttl <= 5, "renew_lease must not extend the cancel flag TTL"


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

    async def test_inject_full_queue_raises_and_recovers(self, store):
        # Mirrors the InMemory contract: full queue raises InjectQueueFull
        # (→ 429), and draining makes it transient (inject works again).
        from config import config
        from api.services.runtime_store import InjectQueueFull

        mid = "test_msg_q_cap"
        for i in range(config.MAX_INJECT_QUEUE_SIZE):
            await store.inject_message(mid, f"m{i}")
        with pytest.raises(InjectQueueFull):
            await store.inject_message(mid, "overflow")

        drained = await store.drain_messages(mid)
        assert len(drained) == config.MAX_INJECT_QUEUE_SIZE
        await store.inject_message(mid, "after")
        assert await store.drain_messages(mid) == ["after"]


class TestCleanup:
    async def test_cleanup_execution(self, store):
        await store.try_acquire_lease("test_conv_clean", "test_msg_clean")
        await store.mark_engine_interactive("test_conv_clean", "test_msg_clean")
        await store.inject_message("test_msg_clean", "data")

        await store.cleanup_execution("test_conv_clean", "test_msg_clean")

        assert await store.get_leased_message_id("test_conv_clean") is None
        assert await store.get_interactive_message_id("test_conv_clean") is None
        assert await store.drain_messages("test_msg_clean") == []

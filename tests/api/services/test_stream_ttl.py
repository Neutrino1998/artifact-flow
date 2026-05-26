"""
P1#1 回归:Redis stream/meta key 寿命必须 = EXECUTION_TIMEOUT + STREAM_TTL_GRACE,
不能等于裸引擎 deadline。

PR-B 把终态(含 TIMED_OUT)的产出移到了引擎 deadline 之后的 post-processing。若 key
TTL 仍等于 deadline,meta_key 会在终态被 push 之前过期 → push_event 落在已过期 key 上
→ 终态丢失 / SSE 挂住。本测试钉死 TTL 与 deadline 解耦。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.redis_stream_transport import RedisStreamTransport


def _make_transport(execution_timeout: int, ttl_grace: int) -> RedisStreamTransport:
    redis = MagicMock()
    redis.hget = AsyncMock(return_value=None)   # 无已存在 stream
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    t = RedisStreamTransport(
        redis,
        key_prefix="af",
        cleanup_ttl=60,
        execution_timeout=execution_timeout,
        ttl_grace=ttl_grace,
    )
    return t


def test_stream_ttl_includes_grace():
    """key TTL = deadline + grace,而非裸 deadline。"""
    t = _make_transport(execution_timeout=1800, ttl_grace=300)
    assert t._stream_ttl == 2100


def test_stream_ttl_default_grace_zero_is_backwards_compatible():
    t = _make_transport(execution_timeout=1800, ttl_grace=0)
    assert t._stream_ttl == 1800


@pytest.mark.asyncio
async def test_create_stream_expires_meta_with_graced_ttl():
    """create_stream 给 meta_key 设的 TTL 必须是 graced 值,不是裸 deadline。"""
    t = _make_transport(execution_timeout=100, ttl_grace=50)
    await t.create_stream("msg-1", owner_user_id="u1")

    # expire(meta_key, ttl) —— ttl 必须是 150,不能是 100
    assert t._redis.expire.await_count == 1
    _meta_key, ttl = t._redis.expire.await_args.args
    assert ttl == 150, (
        f"meta_key TTL={ttl} 等于裸 deadline 而非 graced 值 → P1#1: "
        f"key 会在终态 push 前过期"
    )

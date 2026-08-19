"""Anonymous SSO start admission contracts."""

from __future__ import annotations

import pytest

from api.services.sso_rate_limiter import (
    InMemorySsoStartRateLimiter,
    RedisSsoStartRateLimiter,
    SsoStartRateLimitError,
)


class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self._script_count = 0

    def register_script(self, _script):
        self._script_count += 1
        script_index = self._script_count

        async def run(*, keys, args):
            key = keys[0]
            limit = int(args[0])
            current = self.counts.get(key, 0)
            if script_index == 2:
                return 60_000 if current >= limit else None
            if current >= limit:
                return [0, 60_000]
            self.counts[key] = current + 1
            return [1, 60_000]

        return run


async def test_inmemory_limiter_separates_ip_and_global_exhaustion():
    ip_limiter = InMemorySsoStartRateLimiter(
        per_ip_limit=1, global_limit=10, window_seconds=60, clock=lambda: 100.0
    )
    await ip_limiter.admit("10.0.0.1")
    with pytest.raises(SsoStartRateLimitError) as ip_error:
        await ip_limiter.admit("10.0.0.1")
    assert ip_error.value.scope == "ip"

    global_limiter = InMemorySsoStartRateLimiter(
        per_ip_limit=10, global_limit=1, window_seconds=60, clock=lambda: 100.0
    )
    await global_limiter.admit("10.0.0.1")
    with pytest.raises(SsoStartRateLimitError) as global_error:
        await global_limiter.admit("10.0.0.2")
    assert global_error.value.scope == "global"


async def test_inmemory_limiter_window_expires():
    now = [100.0]
    limiter = InMemorySsoStartRateLimiter(
        per_ip_limit=1,
        global_limit=1,
        window_seconds=60,
        clock=lambda: now[0],
    )
    await limiter.admit("10.0.0.1")
    now[0] = 161.0
    await limiter.admit("10.0.0.1")


async def test_redis_limiter_uses_shared_global_and_hashed_ip_keys():
    redis = _FakeRedis()
    limiter = RedisSsoStartRateLimiter(
        redis,
        per_ip_limit=1,
        global_limit=10,
        window_seconds=60,
        key_prefix="tenant",
    )
    await limiter.admit("10.0.0.1")

    assert redis.counts["tenant:sso:start:global:all"] == 1
    ip_keys = [key for key in redis.counts if ":ip:" in key]
    assert len(ip_keys) == 1
    assert "10.0.0.1" not in ip_keys[0]

    with pytest.raises(SsoStartRateLimitError) as exc_info:
        await limiter.admit("10.0.0.1")
    assert exc_info.value.scope == "ip"
    # The precheck keeps a blocked address from draining global permits.
    assert redis.counts["tenant:sso:start:global:all"] == 1

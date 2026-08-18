"""Anonymous SSO start admission with shared global and per-IP windows.

Every Redis operation touches exactly one key.  The global and IP decisions are
therefore separate single-key script calls, which keeps the implementation safe
for standalone Redis, Sentinel, and Cluster deployments.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from typing import Callable, Literal


_LUA_ADMIT = """
local current = redis.call('GET', KEYS[1])
if current and tonumber(current) >= tonumber(ARGV[1]) then
  return {0, redis.call('PTTL', KEYS[1])}
end
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return {1, redis.call('PTTL', KEYS[1])}
"""

_LUA_RETRY_AFTER = """
local current = redis.call('GET', KEYS[1])
if not current or tonumber(current) < tonumber(ARGV[1]) then
  return nil
end
return redis.call('PTTL', KEYS[1])
"""


class SsoStartRateLimitError(RuntimeError):
    """The caller or the complete deployment exhausted start admission."""

    def __init__(self, scope: Literal["ip", "global"], retry_after: int):
        self.scope = scope
        self.retry_after = retry_after
        super().__init__(f"SSO start {scope} admission exhausted")


def _seconds_from_pttl(pttl: int | None, window_seconds: int) -> int | None:
    if pttl is None:
        return None
    pttl = int(pttl)
    if pttl > 0:
        return max(1, math.ceil(pttl / 1000))
    if pttl == 0:
        return 1
    if pttl == -2:
        return None
    # A live admission key without expiry is an invalid state. Fail closed for
    # one complete configured window instead of silently disabling the guard.
    return window_seconds


def _ip_key(client_ip: str) -> str:
    return hashlib.sha256(client_ip.encode("utf-8")).hexdigest()


class RedisSsoStartRateLimiter:
    """Cross-worker fixed-window admission backed by Redis."""

    def __init__(
        self,
        redis,
        *,
        per_ip_limit: int,
        global_limit: int,
        window_seconds: int,
        key_prefix: str = "",
    ):
        if min(per_ip_limit, global_limit, window_seconds) <= 0:
            raise ValueError("SSO start rate limits and window must be positive")
        self._redis = redis
        self._per_ip_limit = per_ip_limit
        self._global_limit = global_limit
        self._window_seconds = window_seconds
        self._window_ms = window_seconds * 1000
        self._prefix = key_prefix
        self._admit_script = redis.register_script(_LUA_ADMIT)
        self._retry_script = redis.register_script(_LUA_RETRY_AFTER)

    def _key(self, scope: Literal["ip", "global"], value: str) -> str:
        base = f"sso:start:{scope}:{value}"
        return f"{self._prefix}:{base}" if self._prefix else base

    async def _retry_after(self, key: str, limit: int) -> int | None:
        pttl = await self._retry_script(keys=[key], args=[limit])
        return _seconds_from_pttl(pttl, self._window_seconds)

    async def _admit(self, key: str, limit: int) -> tuple[bool, int]:
        allowed, pttl = await self._admit_script(
            keys=[key], args=[limit, self._window_ms]
        )
        retry_after = _seconds_from_pttl(pttl, self._window_seconds)
        return bool(int(allowed)), retry_after or self._window_seconds

    async def admit(self, client_ip: str) -> None:
        ip_key = self._key("ip", _ip_key(client_ip))

        # Avoid letting an already-blocked address consume global permits. The
        # subsequent atomic admit still resolves concurrent requests correctly.
        retry_after = await self._retry_after(ip_key, self._per_ip_limit)
        if retry_after is not None:
            raise SsoStartRateLimitError("ip", retry_after)

        allowed, retry_after = await self._admit(
            self._key("global", "all"), self._global_limit
        )
        if not allowed:
            raise SsoStartRateLimitError("global", retry_after)

        allowed, retry_after = await self._admit(ip_key, self._per_ip_limit)
        if not allowed:
            # A small number of simultaneous requests may consume global
            # permits before this per-IP decision. Conservatively retaining
            # those permits avoids cross-slot rollback machinery.
            raise SsoStartRateLimitError("ip", retry_after)


class InMemorySsoStartRateLimiter:
    """Single-process development implementation with the same window contract."""

    def __init__(
        self,
        *,
        per_ip_limit: int,
        global_limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        if min(per_ip_limit, global_limit, window_seconds) <= 0:
            raise ValueError("SSO start rate limits and window must be positive")
        self._per_ip_limit = per_ip_limit
        self._global_limit = global_limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._records: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    def _record(self, key: str, now: float) -> tuple[int, float] | None:
        record = self._records.get(key)
        if record is not None and record[1] <= now:
            self._records.pop(key, None)
            return None
        return record

    def _admit(self, key: str, limit: int, now: float) -> int | None:
        record = self._record(key, now)
        if record is not None and record[0] >= limit:
            return max(1, math.ceil(record[1] - now))
        if record is None:
            self._records[key] = (1, now + self._window_seconds)
        else:
            self._records[key] = (record[0] + 1, record[1])
        return None

    async def admit(self, client_ip: str) -> None:
        now = self._clock()
        ip_key = f"ip:{_ip_key(client_ip)}"
        async with self._lock:
            ip_record = self._record(ip_key, now)
            if ip_record is not None and ip_record[0] >= self._per_ip_limit:
                raise SsoStartRateLimitError(
                    "ip", max(1, math.ceil(ip_record[1] - now))
                )

            retry_after = self._admit("global", self._global_limit, now)
            if retry_after is not None:
                raise SsoStartRateLimitError("global", retry_after)

            retry_after = self._admit(ip_key, self._per_ip_limit, now)
            if retry_after is not None:
                raise SsoStartRateLimitError("ip", retry_after)

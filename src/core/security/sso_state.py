"""Short-lived, browser-bound, one-time SSO handshake state."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Protocol


_LUA_ISSUE = """
local now = redis.call('TIME')
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
  return -1
end
local added = redis.call('ZADD', KEYS[1], 'NX', now_ms + tonumber(ARGV[3]), ARGV[1])
if added == 0 then
  return 0
end
local key_ttl = redis.call('PTTL', KEYS[1])
if key_ttl < tonumber(ARGV[3]) then
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]))
end
return 1
"""

_LUA_CONSUME = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score then
  return 0
end
local now = redis.call('TIME')
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
if tonumber(score) <= now_ms then
  redis.call('ZREM', KEYS[1], ARGV[1])
  return 0
end
return redis.call('ZREM', KEYS[1], ARGV[1])
"""


class SsoStateCapacityError(RuntimeError):
    """The bounded state store cannot admit another handshake."""


@dataclass(frozen=True)
class IssuedSsoState:
    state: str
    browser_binding: str
    expires_in: int


class SsoStateStore(Protocol):
    async def issue(self, ttl_seconds: int) -> IssuedSsoState: ...

    async def consume(self, state: str, browser_binding: str) -> bool: ...


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class InMemorySsoStateStore:
    """Single-process development implementation with a hard admission bound."""

    def __init__(
        self,
        *,
        max_pending: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._max_pending = max_pending
        self._clock = clock
        self._records: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, (_, expires_at) in self._records.items() if expires_at <= now]
        for key in expired:
            self._records.pop(key, None)

    async def issue(self, ttl_seconds: int) -> IssuedSsoState:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        now = self._clock()
        async with self._lock:
            self._purge_expired(now)
            if len(self._records) >= self._max_pending:
                raise SsoStateCapacityError("Too many pending SSO handshakes")
            self._records[_digest(state)] = (_digest(binding), now + ttl_seconds)
        return IssuedSsoState(state, binding, ttl_seconds)

    async def consume(self, state: str, browser_binding: str) -> bool:
        if not state or not browser_binding:
            return False
        key = _digest(state)
        now = self._clock()
        async with self._lock:
            record = self._records.get(key)
            if record is None:
                return False
            expected_binding, expires_at = record
            if expires_at <= now:
                self._records.pop(key, None)
                return False
            if not secrets.compare_digest(expected_binding, _digest(browser_binding)):
                return False
            self._records.pop(key, None)
            return True


class RedisSsoStateStore:
    """Multi-worker implementation with one bounded sorted-set substrate.

    Issue and consume each touch one shared key, so capacity, expiry, and
    one-time removal remain atomic and Redis Cluster safe without a counter that
    could drift from separately expiring state keys.
    """

    def __init__(self, redis, *, max_pending: int = 10_000, key_prefix: str = ""):
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._redis = redis
        self._max_pending = max_pending
        self._prefix = key_prefix
        self._issue_script = redis.register_script(_LUA_ISSUE)
        self._consume_script = redis.register_script(_LUA_CONSUME)

    def _key(self) -> str:
        base = "sso:state:pending"
        return f"{self._prefix}:{base}" if self._prefix else base

    @staticmethod
    def _member(state: str, browser_binding: str) -> str:
        return f"{_digest(state)}:{_digest(browser_binding)}"

    async def issue(self, ttl_seconds: int) -> IssuedSsoState:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        admitted = await self._issue_script(
            keys=[self._key()],
            args=[
                self._member(state, binding),
                self._max_pending,
                ttl_seconds * 1000,
            ],
        )
        if int(admitted) == -1:
            raise SsoStateCapacityError("Too many pending SSO handshakes")
        if int(admitted) != 1:
            # A joint 512-bit state/binding collision is not a capacity signal.
            raise RuntimeError("Could not allocate unique SSO state")
        return IssuedSsoState(state, binding, ttl_seconds)

    async def consume(self, state: str, browser_binding: str) -> bool:
        if not state or not browser_binding:
            return False
        result = await self._consume_script(
            keys=[self._key()], args=[self._member(state, browser_binding)]
        )
        return bool(result)

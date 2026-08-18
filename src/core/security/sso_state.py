"""Short-lived, browser-bound, one-time SSO handshake state."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Protocol


_LUA_CONSUME = """
local value = redis.call('GET', KEYS[1])
if not value or value ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


class SsoStateCapacityError(RuntimeError):
    """The bounded in-memory state store cannot admit another handshake."""


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
    """Multi-worker implementation; consume is one-key atomic compare-and-delete.

    Every operation touches exactly one state key, so it is Redis Cluster safe
    without a shared hash tag or a cross-slot multi-key command.
    """

    def __init__(self, redis, *, key_prefix: str = ""):
        self._redis = redis
        self._prefix = key_prefix
        self._consume_script = redis.register_script(_LUA_CONSUME)

    def _key(self, state: str) -> str:
        base = f"sso:state:{_digest(state)}"
        return f"{self._prefix}:{base}" if self._prefix else base

    async def issue(self, ttl_seconds: int) -> IssuedSsoState:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        admitted = await self._redis.set(
            self._key(state), _digest(binding), ex=ttl_seconds, nx=True
        )
        if not admitted:
            # A 256-bit collision is not a recoverable capacity condition.
            raise RuntimeError("Could not allocate unique SSO state")
        return IssuedSsoState(state, binding, ttl_seconds)

    async def consume(self, state: str, browser_binding: str) -> bool:
        if not state or not browser_binding:
            return False
        result = await self._consume_script(
            keys=[self._key(state)], args=[_digest(browser_binding)]
        )
        return bool(result)

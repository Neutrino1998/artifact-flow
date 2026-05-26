"""登录频控（ACC-01）—— per-username + per-IP 失败计数,超阈临时锁定。

防无节制撞库 / credential stuffing。调用方(login 端点)对两个独立 key
(username 与 client-IP)各跑一遍:验证前预检任一锁定即 429;认证失败对两 key
各 +1;认证成功重置 username key(IP key 自然过期)。

Cluster 安全(见 [[redis-cluster-safety-constraint]]):每个操作都只碰**单个 key**
(INCR+EXPIRE 在同一 key 的单脚本里、GET/DEL 单 key),绝不在一条命令里跨
username-key 与 ip-key → 无跨 slot multi-key,Cluster 模式可直接路由。

两实现镜像 runtime_store 的 Redis / InMemory 双轨:配 REDIS_URL 走 Redis(多
worker 共享计数),否则 InMemory(单进程,够用于 dev / 单机)。

窗口语义:固定窗口 —— 第一次失败时设 TTL=window;窗口内累计,到 max 即锁;
窗口过期 key 消失、计数清零。锁定时长 ≈ 距首次失败的剩余窗口。
"""

from __future__ import annotations

import time
from typing import Optional


# 单 key 原子脚本:INCR,若是第一次(返回 1)则设 TTL。Cluster 安全(单 key)。
_LUA_INCR_FAILURE = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return n
"""


class RedisLoginRateLimiter:
    """Redis 实现 —— 多 worker 共享失败计数。"""

    def __init__(self, redis, max_failures: int, window_sec: int, key_prefix: str = ""):
        self._redis = redis
        self._max = max_failures
        self._window = window_sec
        self._prefix = key_prefix
        # register_script 自动处理 NOSCRIPT 重试(与 redis_runtime_store 同模式)
        self._incr = redis.register_script(_LUA_INCR_FAILURE)

    def _k(self, key: str) -> str:
        base = f"login:fail:{key}"
        return f"{self._prefix}:{base}" if self._prefix else base

    async def is_locked(self, key: str) -> bool:
        n = await self._redis.get(self._k(key))
        return n is not None and int(n) >= self._max

    async def record_failure(self, key: str) -> None:
        await self._incr(keys=[self._k(key)], args=[self._window])

    async def reset(self, key: str) -> None:
        await self._redis.delete(self._k(key))


class InMemoryLoginRateLimiter:
    """进程内实现 —— 无 Redis 时的 fallback(单机 / dev)。"""

    def __init__(self, max_failures: int, window_sec: int):
        self._max = max_failures
        self._window = window_sec
        self._store: dict[str, tuple[int, float]] = {}  # key -> (count, reset_at_monotonic)

    def _live_count(self, key: str) -> Optional[int]:
        rec = self._store.get(key)
        if rec is None:
            return None
        count, reset_at = rec
        if time.monotonic() >= reset_at:
            self._store.pop(key, None)
            return None
        return count

    async def is_locked(self, key: str) -> bool:
        count = self._live_count(key)
        return count is not None and count >= self._max

    async def record_failure(self, key: str) -> None:
        now = time.monotonic()
        rec = self._store.get(key)
        if rec is None or now >= rec[1]:
            self._store[key] = (1, now + self._window)
        else:
            self._store[key] = (rec[0] + 1, rec[1])

    async def reset(self, key: str) -> None:
        self._store.pop(key, None)

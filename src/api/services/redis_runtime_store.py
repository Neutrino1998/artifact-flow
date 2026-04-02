"""
RedisRuntimeStore — Redis-backed RuntimeStore 实现

支持跨 Worker 的 lease/interrupt/cancel/queue 状态共享。
使用 Lua 脚本保证原子性，Pub/Sub 实现 interrupt 唤醒。

Key 设计：
    lease:{conv_id}       STRING (msg_id)   TTL=LEASE_TTL   conversation lease
    interactive:{conv_id} STRING (msg_id)   TTL=LEASE_TTL   engine interactive
    interrupt:{msg_id}    HASH              TTL=PERM+60     interrupt 状态
    cancel:{msg_id}       STRING "1"        TTL=STREAM_TO   取消标记
    queue:{msg_id}        LIST              TTL=STREAM_TO   消息注入队列
"""

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional, Set

import redis.asyncio as aioredis

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# ── Lua 脚本 ──

# compare-and-del: GET key == owner ? DEL : 0
_LUA_COMPARE_AND_DEL = """
local val = redis.call('GET', KEYS[1])
if val == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""

# compare-and-expire: GET key == owner ? EXPIRE : 0
_LUA_COMPARE_AND_EXPIRE = """
local val = redis.call('GET', KEYS[1])
if val == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
else
    return 0
end
"""

# acquire-lease: 原子 SET NX 或返回现有持有者
_LUA_ACQUIRE_LEASE = """
local ok = redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', tonumber(ARGV[2]))
if ok then
    return nil
end
return redis.call('GET', KEYS[1])
"""

# drain-all: LRANGE + DEL 原子取出队列
_LUA_DRAIN_ALL = """
local items = redis.call('LRANGE', KEYS[1], 0, -1)
if #items > 0 then
    redis.call('DEL', KEYS[1])
end
return items
"""

# resolve-interrupt: 检查 status=pending → 设 resume_data + status=resolved → PUBLISH
_LUA_RESOLVE_INTERRUPT = """
local status = redis.call('HGET', KEYS[1], 'status')
if status == nil then
    return 'not_found'
end
if status ~= 'pending' then
    return 'already_resolved'
end
redis.call('HSET', KEYS[1], 'status', 'resolved', 'resume_data', ARGV[1])
redis.call('PUBLISH', ARGV[2], 'resolved')
return 'resolved'
"""


class RedisRuntimeStore:
    """Redis-backed RuntimeStore 实现"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        lease_ttl: int,
        stream_timeout: int,
        permission_timeout: int,
    ):
        self._redis = redis_client
        self._lease_ttl = lease_ttl
        self._stream_timeout = stream_timeout
        self._permission_timeout = permission_timeout

        # Lua script SHA（_init_scripts 中加载）
        self._sha_acquire_lease: str = ""
        self._sha_compare_and_del: str = ""
        self._sha_compare_and_expire: str = ""
        self._sha_drain_all: str = ""
        self._sha_resolve_interrupt: str = ""

        # 本 Worker 已知的 interrupt subscription，用于 shutdown_cleanup
        self._local_subscriptions: Set[str] = set()

    async def init_scripts(self) -> None:
        """SCRIPT LOAD 所有 Lua 脚本（init_globals 时调用一次）"""
        self._sha_acquire_lease = await self._redis.script_load(_LUA_ACQUIRE_LEASE)
        self._sha_compare_and_del = await self._redis.script_load(_LUA_COMPARE_AND_DEL)
        self._sha_compare_and_expire = await self._redis.script_load(_LUA_COMPARE_AND_EXPIRE)
        self._sha_drain_all = await self._redis.script_load(_LUA_DRAIN_ALL)
        self._sha_resolve_interrupt = await self._redis.script_load(_LUA_RESOLVE_INTERRUPT)
        logger.info("Redis Lua scripts loaded")

    # ── Key helpers ──

    @staticmethod
    def _lease_key(conversation_id: str) -> str:
        return f"lease:{conversation_id}"

    @staticmethod
    def _interactive_key(conversation_id: str) -> str:
        return f"interactive:{conversation_id}"

    @staticmethod
    def _interrupt_key(message_id: str) -> str:
        return f"interrupt:{message_id}"

    @staticmethod
    def _cancel_key(message_id: str) -> str:
        return f"cancel:{message_id}"

    @staticmethod
    def _queue_key(message_id: str) -> str:
        return f"queue:{message_id}"

    @staticmethod
    def _interrupt_channel(message_id: str) -> str:
        return f"interrupt_ch:{message_id}"

    # ── Conversation lease ──

    async def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]:
        key = self._lease_key(conversation_id)
        # 原子操作：SET NX 成功返回 nil，否则返回现有持有者
        # 消除了原先 SET NX + GET 之间的竞态窗口
        result = await self._redis.evalsha(
            self._sha_acquire_lease, 1, key, message_id, str(self._lease_ttl)
        )
        return result  # None = acquired, str = existing owner

    async def release_lease(self, conversation_id: str, message_id: str) -> None:
        key = self._lease_key(conversation_id)
        await self._redis.evalsha(
            self._sha_compare_and_del, 1, key, message_id
        )

    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]:
        return await self._redis.get(self._lease_key(conversation_id))

    # ── Engine interactive ──

    async def mark_engine_interactive(self, conversation_id: str, message_id: str) -> None:
        key = self._interactive_key(conversation_id)
        await self._redis.set(key, message_id, ex=self._lease_ttl)

    async def clear_engine_interactive(self, conversation_id: str, message_id: str) -> None:
        key = self._interactive_key(conversation_id)
        await self._redis.evalsha(
            self._sha_compare_and_del, 1, key, message_id
        )

    async def get_interactive_message_id(self, conversation_id: str) -> Optional[str]:
        return await self._redis.get(self._interactive_key(conversation_id))

    # ── Interrupts ──

    async def wait_for_interrupt(
        self, message_id: str, data: Dict[str, Any], timeout: float
    ) -> Optional[Dict[str, Any]]:
        """
        创建 interrupt 并阻塞等待恢复。

        使用 check-subscribe-check-wait 四步模式防 Pub/Sub 丢通知：
        1. HSET 创建 interrupt（status=pending）
        2. SUBSCRIBE channel
        3. 再次 HGET 检查 status（如果在 1-2 之间已 resolve，直接返回）
        4. 等待 PUBLISH 通知或超时
        """
        interrupt_key = self._interrupt_key(message_id)
        channel_name = self._interrupt_channel(message_id)
        interrupt_ttl = int(timeout) + 60

        # Step 1: 创建 interrupt hash
        await self._redis.hset(interrupt_key, mapping={
            "data": json.dumps(data),
            "status": "pending",
            "resume_data": "",
        })
        await self._redis.expire(interrupt_key, interrupt_ttl)

        self._local_subscriptions.add(message_id)
        logger.info(f"Interrupt created for {message_id}")

        pubsub = self._redis.pubsub()
        try:
            # Step 2: Subscribe
            await pubsub.subscribe(channel_name)

            # Step 3: Re-check（防止 1→2 窗口内已 resolve）
            status = await self._redis.hget(interrupt_key, "status")
            if status == "resolved":
                resume_raw = await self._redis.hget(interrupt_key, "resume_data")
                self._local_subscriptions.discard(message_id)
                return json.loads(resume_raw) if resume_raw else None

            # Step 4: Wait for PUBLISH or timeout
            try:
                async with asyncio.timeout(timeout):
                    while True:
                        msg = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        )
                        if msg and msg["type"] == "message":
                            break
            except TimeoutError:
                self._local_subscriptions.discard(message_id)
                return None

            # 读取 resume_data
            resume_raw = await self._redis.hget(interrupt_key, "resume_data")
            self._local_subscriptions.discard(message_id)
            return json.loads(resume_raw) if resume_raw else None

        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.aclose()

    async def resolve_interrupt(
        self, message_id: str, resume_data: Dict[str, Any]
    ) -> Literal["resolved", "not_found", "already_resolved"]:
        interrupt_key = self._interrupt_key(message_id)
        channel_name = self._interrupt_channel(message_id)
        resume_json = json.dumps(resume_data)

        result = await self._redis.evalsha(
            self._sha_resolve_interrupt,
            1, interrupt_key,
            resume_json, channel_name,
        )
        # evalsha returns bytes when decode_responses=True → str
        status = result if isinstance(result, str) else result.decode()
        logger.info(f"Interrupt resolve for {message_id}: {status}")
        return status  # type: ignore[return-value]

    async def get_interrupt_data(self, message_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._redis.hget(self._interrupt_key(message_id), "data")
        if raw is None:
            return None
        return json.loads(raw)

    # ── Cancellation ──

    async def request_cancel(self, message_id: str) -> None:
        await self._redis.set(
            self._cancel_key(message_id), "1", ex=self._stream_timeout
        )
        # 同时唤醒可能阻塞的 interrupt（与 InMemory 行为一致）
        interrupt_key = self._interrupt_key(message_id)
        channel_name = self._interrupt_channel(message_id)
        cancel_data = json.dumps({"approved": False, "reason": "cancelled"})
        await self._redis.evalsha(
            self._sha_resolve_interrupt,
            1, interrupt_key,
            cancel_data, channel_name,
        )
        logger.info(f"Cancellation requested for {message_id}")

    async def is_cancelled(self, message_id: str) -> bool:
        try:
            val = await self._redis.get(self._cancel_key(message_id))
            return val is not None
        except aioredis.ConnectionError:
            logger.warning(f"Redis unavailable checking cancel for {message_id}, treating as not cancelled")
            return False

    # ── Message queue ──

    async def inject_message(self, message_id: str, content: str) -> None:
        key = self._queue_key(message_id)
        await self._redis.rpush(key, content)
        await self._redis.expire(key, self._stream_timeout)
        logger.debug(f"Message injected for {message_id}")

    async def drain_messages(self, message_id: str) -> List[str]:
        try:
            key = self._queue_key(message_id)
            items = await self._redis.evalsha(self._sha_drain_all, 1, key)
            return list(items) if items else []
        except aioredis.ConnectionError:
            logger.warning(f"Redis unavailable draining messages for {message_id}, returning empty")
            return []

    # ── Lifecycle ──

    async def cleanup_execution(self, conversation_id: str, message_id: str) -> None:
        """清理指定 message_id 的所有运行时 key"""
        pipe = self._redis.pipeline(transaction=False)
        pipe.delete(self._interrupt_key(message_id))
        pipe.delete(self._cancel_key(message_id))
        pipe.delete(self._queue_key(message_id))
        # lease 和 interactive：compare-and-del（只删自己持有的）
        pipe.evalsha(
            self._sha_compare_and_del, 1,
            self._lease_key(conversation_id), message_id,
        )
        pipe.evalsha(
            self._sha_compare_and_del, 1,
            self._interactive_key(conversation_id), message_id,
        )
        await pipe.execute()
        self._local_subscriptions.discard(message_id)
        logger.debug(f"Execution {message_id} cleaned up from Redis")

    async def shutdown_cleanup(self) -> None:
        """关闭时清理：resolve 本 Worker 已知的 pending interrupt"""
        for message_id in list(self._local_subscriptions):
            try:
                await self.resolve_interrupt(
                    message_id, {"approved": False, "reason": "shutdown"}
                )
            except Exception:
                logger.warning(f"Failed to resolve interrupt {message_id} during shutdown")
        self._local_subscriptions.clear()
        logger.debug("Redis runtime store shutdown cleanup complete")

    async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool:
        """心跳续租 — 校验 owner 后 EXPIRE lease + interactive。

        Returns True if the lease key was successfully renewed (still owner),
        False if the lease was lost (expired or taken over by another worker).
        """
        ttl_int = int(ttl)
        pipe = self._redis.pipeline(transaction=False)
        pipe.evalsha(
            self._sha_compare_and_expire, 1,
            self._lease_key(conversation_id), message_id, str(ttl_int),
        )
        pipe.evalsha(
            self._sha_compare_and_expire, 1,
            self._interactive_key(conversation_id), message_id, str(ttl_int),
        )
        results = await pipe.execute()
        # results[0] == 1 means lease key EXPIRE succeeded (we're still owner)
        return results[0] == 1

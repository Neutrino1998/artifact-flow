"""
RedisRuntimeStore — Redis-backed RuntimeStore 实现

支持跨 Worker 的 lease/interrupt/cancel/queue 状态共享。
使用 Lua 脚本保证原子性，Pub/Sub 实现 interrupt 唤醒。

Key 设计（{prefix:id} 为 hash tag，确保同 entity 同 slot）：
    {prefix:conv_id}:lease        STRING (msg_id)   TTL=LEASE_TTL   conversation lease
    {prefix:conv_id}:interactive  STRING (msg_id)   TTL=LEASE_TTL   engine interactive
    {prefix:msg_id}:interrupt     HASH              TTL=PERM+60     interrupt 状态
    {prefix:msg_id}:cancel        STRING "1"        TTL=EXEC_TO     取消标记
    {prefix:msg_id}:queue         LIST              TTL=EXEC_TO     消息注入队列
"""

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional, Set

import redis.asyncio as aioredis

from config import config
from api.services.runtime_store import InjectQueueFull
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

# mark-interactive-if-owner: 仅当 lease 仍归 ARGV[1] 时才 SET interactive。
# QUEUED→RUNNING 边的原子 compare-and-set —— 防止「排队中丢了 lease 的旧 task
# 取得 semaphore 后覆盖新 owner 的 interactive key」（misroute inject + 跑无 fence 旧轮）。
# 不是 owner 返回 0 → runner 据此 abort，不启动引擎（避免第二写者）。
# KEYS[1]=lease_key, KEYS[2]=interactive_key —— 二者都 {prefix:conv_id} hash-tag、
# 同 slot，多 key Lua 在 Cluster 安全。ARGV[1]=msg_id, ARGV[2]=ttl
_LUA_MARK_INTERACTIVE_IF_OWNER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[2], ARGV[1], 'EX', tonumber(ARGV[2]))
    return 1
end
return 0
"""

# drain-all: LRANGE + DEL 原子取出队列
_LUA_DRAIN_ALL = """
local items = redis.call('LRANGE', KEYS[1], 0, -1)
if #items > 0 then
    redis.call('DEL', KEYS[1])
end
return items
"""

# inject-capped: 原子地 LLEN < cap ? RPUSH + EXPIRE → 新长度 : -1（满）
# 等价于 InMemory 的 put_nowait(maxsize) —— 队列满返回 -1，调用方抛 InjectQueueFull → 429。
_LUA_INJECT_CAPPED = """
local len = redis.call('LLEN', KEYS[1])
if len >= tonumber(ARGV[2]) then
    return -1
end
redis.call('RPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return len + 1
"""

# resolve-interrupt: 检查 status=pending → 设 resume_data + status=resolved → PUBLISH
_LUA_RESOLVE_INTERRUPT = """
local status = redis.call('HGET', KEYS[1], 'status')
if not status then
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
        execution_timeout: int,
        permission_timeout: int,
        key_prefix: str,
    ):
        self._redis = redis_client
        self._lease_ttl = lease_ttl
        self._execution_timeout = execution_timeout
        self._permission_timeout = permission_timeout
        self._prefix = key_prefix

        # Lua scripts（register_script 对象，自动处理 NOSCRIPT 重试）
        self._script_acquire_lease = None
        self._script_mark_interactive_if_owner = None
        self._script_compare_and_del = None
        self._script_compare_and_expire = None
        self._script_drain_all = None
        self._script_inject_capped = None
        self._script_resolve_interrupt = None

        # 本 Worker 已知的 interrupt subscription，用于 shutdown_cleanup
        self._local_subscriptions: Set[str] = set()

    def init_scripts(self) -> None:
        """注册所有 Lua 脚本（register_script 是同步方法，自动处理 NOSCRIPT 重试）"""
        self._script_acquire_lease = self._redis.register_script(_LUA_ACQUIRE_LEASE)
        self._script_mark_interactive_if_owner = self._redis.register_script(_LUA_MARK_INTERACTIVE_IF_OWNER)
        self._script_compare_and_del = self._redis.register_script(_LUA_COMPARE_AND_DEL)
        self._script_compare_and_expire = self._redis.register_script(_LUA_COMPARE_AND_EXPIRE)
        self._script_drain_all = self._redis.register_script(_LUA_DRAIN_ALL)
        self._script_inject_capped = self._redis.register_script(_LUA_INJECT_CAPPED)
        self._script_resolve_interrupt = self._redis.register_script(_LUA_RESOLVE_INTERRUPT)
        logger.info("Redis Lua scripts registered")

    # ── Key helpers ──

    def _lease_key(self, conversation_id: str) -> str:
        return f"{{{self._prefix}:{conversation_id}}}:lease"

    def _interactive_key(self, conversation_id: str) -> str:
        return f"{{{self._prefix}:{conversation_id}}}:interactive"

    def _interrupt_key(self, message_id: str) -> str:
        return f"{{{self._prefix}:{message_id}}}:interrupt"

    def _cancel_key(self, message_id: str) -> str:
        return f"{{{self._prefix}:{message_id}}}:cancel"

    def _queue_key(self, message_id: str) -> str:
        return f"{{{self._prefix}:{message_id}}}:queue"

    def _interrupt_channel(self, message_id: str) -> str:
        return f"{{{self._prefix}:{message_id}}}:interrupt_ch"

    # ── Conversation lease ──

    async def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]:
        key = self._lease_key(conversation_id)
        # 原子操作：SET NX 成功返回 nil，否则返回现有持有者
        # 消除了原先 SET NX + GET 之间的竞态窗口
        result = await self._script_acquire_lease(
            keys=[key], args=[message_id, str(self._lease_ttl)]
        )
        return result  # None = acquired, str = existing owner

    async def release_lease(self, conversation_id: str, message_id: str) -> None:
        key = self._lease_key(conversation_id)
        await self._script_compare_and_del(keys=[key], args=[message_id])

    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]:
        return await self._redis.get(self._lease_key(conversation_id))

    # ── Engine interactive ──

    async def mark_engine_interactive(self, conversation_id: str, message_id: str) -> bool:
        """Mark RUNNING **only if** this message still owns the conversation lease.

        Atomic compare-and-set (lease owner → SET interactive). Returns True if
        marked (still owner), False if the lease was lost/taken over while queued
        — in which case the runner must abort instead of clobbering the new
        owner's interactive key and running a second writer on the conversation.
        """
        result = await self._script_mark_interactive_if_owner(
            keys=[self._lease_key(conversation_id), self._interactive_key(conversation_id)],
            args=[message_id, str(self._lease_ttl)],
        )
        return result == 1

    async def clear_engine_interactive(self, conversation_id: str, message_id: str) -> None:
        key = self._interactive_key(conversation_id)
        await self._script_compare_and_del(keys=[key], args=[message_id])

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
                        try:
                            msg = await pubsub.get_message(
                                ignore_subscribe_messages=True, timeout=1.0
                            )
                        except (aioredis.ConnectionError, aioredis.TimeoutError):
                            logger.warning(
                                f"Redis Pub/Sub connection lost for {message_id}, "
                                "treating as timeout deny"
                            )
                            self._local_subscriptions.discard(message_id)
                            return None
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

        result = await self._script_resolve_interrupt(
            keys=[interrupt_key], args=[resume_json, channel_name]
        )
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
            self._cancel_key(message_id), "1", ex=self._execution_timeout
        )
        # 同时唤醒可能阻塞的 interrupt（与 InMemory 行为一致）
        interrupt_key = self._interrupt_key(message_id)
        channel_name = self._interrupt_channel(message_id)
        cancel_data = json.dumps({"approved": False, "reason": "cancelled"})
        await self._script_resolve_interrupt(
            keys=[interrupt_key], args=[cancel_data, channel_name]
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
        # Atomic LLEN/RPUSH/EXPIRE via Lua — no TOCTOU between the cap check and
        # the push. Returns -1 when the queue is already at MAX_INJECT_QUEUE_SIZE.
        # (Redis-down propagates as ConnectionError → 5xx; we do NOT silently drop
        # an inject, mirroring the loud-failure stance of the rest of the path.)
        key = self._queue_key(message_id)
        result = await self._script_inject_capped(
            keys=[key],
            args=[content, config.MAX_INJECT_QUEUE_SIZE, self._execution_timeout],
        )
        if result == -1:
            raise InjectQueueFull(
                f"Inject queue full for {message_id} "
                f"(max {config.MAX_INJECT_QUEUE_SIZE} pending)"
            )
        logger.debug(f"Message injected for {message_id}")

    async def drain_messages(self, message_id: str) -> List[str]:
        try:
            key = self._queue_key(message_id)
            items = await self._script_drain_all(keys=[key])
            return list(items) if items else []
        except aioredis.ConnectionError:
            logger.warning(f"Redis unavailable draining messages for {message_id}, returning empty")
            return []

    # ── Owner-key primitives ──

    def _prefixed(self, key: str) -> str:
        """Add hash-tagged prefix for Cluster slot routing."""
        return f"{{{self._prefix}:{key}}}"

    # ── Active conversations ──

    async def list_active_conversations(self) -> List[str]:
        """Scan for active conversation leases and return conversation IDs."""
        pattern = f"{{{self._prefix}:*}}:lease"
        conv_ids: List[str] = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            # Key format: {prefix:conv_id}:lease
            k = key if isinstance(key, str) else key.decode()
            # Extract conv_id from {prefix:conv_id}:lease
            start = k.index(":") + 1  # after prefix:
            end = k.index("}")
            conv_ids.append(k[start:end])
        return conv_ids

    async def list_active_executions(self) -> Dict[str, str]:
        """Scan lease keys + pipelined GET to return {conv_id: message_id}.

        Cluster-safety: lease keys are hash-tagged by conv_id, so distinct
        conversations land on distinct Cluster slots. A single MGET is a
        single-slot primitive → it raises CROSSSLOT on Cluster. We instead
        fan out per-key GETs through a non-transactional pipeline: redis-py
        routes each GET to its owning node (Cluster splits by node;
        standalone/Sentinel send them back-to-back), so the same code is
        correct on every deployment form. (mget_nonatomic is rejected: it is
        a RedisCluster-only method and would AttributeError on a standalone
        client — see CLAUDE.md "Redis Cluster-safety".)

        Two-step (scan, then GET batch) is good enough at our scale — the lease
        set is bounded by MAX_CONCURRENT_TASKS so the batch is tiny. A lease
        that expires between scan and GET drops out of the map naturally (None
        value), which is the desired semantics ("no longer active").
        """
        pattern = f"{{{self._prefix}:*}}:lease"
        keys: List[str] = []
        conv_ids: List[str] = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            k = key if isinstance(key, str) else key.decode()
            start = k.index(":") + 1
            end = k.index("}")
            keys.append(k)
            conv_ids.append(k[start:end])
        if not keys:
            return {}
        pipe = self._redis.pipeline(transaction=False)
        for k in keys:
            pipe.get(k)
        values = await pipe.execute()  # results in command order
        result: Dict[str, str] = {}
        for conv_id, raw in zip(conv_ids, values):
            if raw is None:
                continue
            msg_id = raw if isinstance(raw, str) else raw.decode()
            result[conv_id] = msg_id
        return result

    # ── Lease key ──

    def get_lease_key(self, conversation_id: str) -> str:
        """Return the Redis key used for conversation lease (for stream transport lease check)."""
        return self._lease_key(conversation_id)

    # ── Lifecycle ──

    async def cleanup_execution(self, conversation_id: str, message_id: str) -> None:
        """清理指定 message_id 的所有运行时 key"""
        await self._redis.delete(
            self._interrupt_key(message_id),
            self._cancel_key(message_id),
            self._queue_key(message_id),
        )
        # lease 和 interactive：compare-and-del（只删自己持有的）
        await self._script_compare_and_del(
            keys=[self._lease_key(conversation_id)], args=[message_id]
        )
        await self._script_compare_and_del(
            keys=[self._interactive_key(conversation_id)], args=[message_id]
        )
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
        lease_result = await self._script_compare_and_expire(
            keys=[self._lease_key(conversation_id)],
            args=[message_id, str(ttl_int)],
        )
        await self._script_compare_and_expire(
            keys=[self._interactive_key(conversation_id)],
            args=[message_id, str(ttl_int)],
        )
        # No cancel-flag renewal: cancel only ever targets a RUNNING turn (gated on
        # interactive), whose engine reads the flag within seconds — the flag never
        # has to outlive the worker-local queue wait, so EX=EXECUTION_TIMEOUT is
        # always sufficient and needs no heartbeat coupling.
        # lease_result == 1 means lease key EXPIRE succeeded (we're still owner)
        return lease_result == 1

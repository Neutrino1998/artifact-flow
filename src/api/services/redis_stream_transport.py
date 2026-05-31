"""
RedisStreamTransport — Redis Streams-backed StreamTransport 实现

支持跨 Worker 的事件 push/consume。
使用 Redis Streams (XADD/XREAD) 实现事件缓冲和消费。

Key 设计（{prefix:id} 为 hash tag，确保同 entity 同 slot）：
    {prefix:msg_id}:stream       STREAM    TTL=EXECUTION_TIMEOUT+STREAM_TTL_GRACE  事件流
    {prefix:msg_id}:stream_meta  HASH      TTL=EXECUTION_TIMEOUT+STREAM_TTL_GRACE  stream 元数据 {owner, status}

注意 TTL 含 STREAM_TTL_GRACE：key 必须活过引擎 deadline 之后的 post-processing
（终态——含 TIMED_OUT——在那时才 push），否则 push_event 落在已过期 key 上 → 终态丢失。
"""

import asyncio
import json
import os
from typing import Any, AsyncGenerator, Dict, Literal, Optional

import redis.asyncio as aioredis

from api.services.stream_transport import StreamAlreadyExistsError, StreamNotFoundError
from utils.logger import get_logger, get_request_id

logger = get_logger("ArtifactFlow")

# 终结事件类型(consumer 见到即 return)。本地副本——传输层不依赖执行语义;
# 与 core.events.TERMINAL_EVENT_TYPES 的一致性由 tests/core/test_terminal_event_sync.py 守护。
_TERMINAL_EVENTS = frozenset(("complete", "cancelled", "timed_out", "error"))

# Lua CAS: 仅当 consumer_id 匹配 且 status 仍为 streaming 时回退到 pending。
# 不缩短 TTL — stream 生命周期由 stream TTL（EXECUTION_TIMEOUT+STREAM_TTL_GRACE）决定，consumer 断连不影响。
# 双重条件防止两类竞态：
#   - consumer_id 不匹配 → 新 consumer 已接管，旧 finally 不动
#   - status != streaming → producer 已 close，consumer finally 不动
# 单 key 操作（KEYS[1]=meta_key），避免 Redis Cluster CROSSSLOT。
# KEYS[1]=meta_key, ARGV[1]=consumer_id
_LUA_REVERT_TO_PENDING = """
local cid = redis.call('HGET', KEYS[1], 'consumer_id')
local status = redis.call('HGET', KEYS[1], 'status')
if cid == ARGV[1] and status == 'streaming' then
    redis.call('HSET', KEYS[1], 'status', 'pending', 'consumer_id', '')
    return 1
end
return 0
"""

# XADD + 首次 EXPIRE 原子合一。pipeline(transaction=False) 只是批量发送、非原子：
# 半包只送到 XADD 而没送到 EXPIRE（连接中途断/failover）会留下无 TTL 的孤儿
# stream key（永不自愈）。Lua 整段原子执行 → 要么 XADD+EXPIRE 都发生、要么键根本没创建。
# 判据 TTL==-1（键在但无过期）才设 TTL：既识别首推，又自愈任何历史遗留的无 TTL 键，
# 且保留「后续推送不刷新 TTL」（不延长 stream 寿命）。
# 单 key 操作（KEYS[1]=stream_key），Cluster 安全。
# 与 meta_key 剩余 TTL 的精确对齐是 best-effort 之外的目标，留给 PR-C 的生命周期重构。
# KEYS[1]=stream_key, ARGV[1]=event_type, ARGV[2]=event_json, ARGV[3]=ttl
_LUA_XADD_WITH_TTL = """
local id = redis.call('XADD', KEYS[1], 'MAXLEN', '~', '1000', '*',
                      'type', ARGV[1], 'data', ARGV[2])
if redis.call('TTL', KEYS[1]) == -1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
end
return id
"""


class RedisStreamTransport:
    """Redis Streams-backed StreamTransport 实现"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        key_prefix: str,
        cleanup_ttl: int = 60,
        execution_timeout: int = 1800,
        ttl_grace: int = 0,
    ):
        self._redis = redis_client
        self._cleanup_ttl = cleanup_ttl
        # stream/meta key 寿命 = 引擎 deadline + 余量,必须覆盖 deadline 之后的
        # post-processing(终态在那时才 push)。不要用裸 execution_timeout 当 key TTL。
        self._stream_ttl = execution_timeout + ttl_grace
        self._prefix = key_prefix
        self._script_revert_to_pending = None
        self._script_xadd_with_ttl = None

    def init_scripts(self) -> None:
        """注册 Lua 脚本（register_script 是同步方法，自动处理 NOSCRIPT 重试）"""
        self._script_revert_to_pending = self._redis.register_script(
            _LUA_REVERT_TO_PENDING
        )
        self._script_xadd_with_ttl = self._redis.register_script(
            _LUA_XADD_WITH_TTL
        )

    # ── Key helpers ──

    def _stream_key(self, stream_id: str) -> str:
        return f"{{{self._prefix}:{stream_id}}}:stream"

    def _meta_key(self, stream_id: str) -> str:
        return f"{{{self._prefix}:{stream_id}}}:stream_meta"

    # ── Protocol methods ──

    async def create_stream(self, stream_id: str, owner_user_id: Optional[str] = None, lease_check_key: Optional[str] = None, lease_expected_owner: Optional[str] = None) -> None:
        meta_key = self._meta_key(stream_id)

        # 检查是否已存在且未关闭
        existing_status = await self._redis.hget(meta_key, "status")
        if existing_status is not None and existing_status != "closed":
            raise StreamAlreadyExistsError(stream_id)

        # 创建元数据 (带 TTL，前端不连接时自动清理)
        await self._redis.hset(meta_key, mapping={
            "owner": owner_user_id or "",
            "status": "pending",
            "lease_check_key": lease_check_key or "",
            "lease_expected_owner": lease_expected_owner or "",
        })
        await self._redis.expire(meta_key, self._stream_ttl)
        # 注意：不对尚未创建的 stream key 做 EXPIRE（EXPIRE 对不存在的 key 是 no-op，
        # 会导致后续 XADD 创建的 key 没有 TTL → 孤儿 key）。
        # stream key 的 TTL 在 push_event 首次 XADD 后设置。

        logger.debug(f"Created Redis stream: {stream_id}")

    async def push_event(self, stream_id: str, event: Dict[str, Any]) -> bool:
        meta_key = self._meta_key(stream_id)
        # ⚠️ 已知竞态窗口：HGET → XADD 之间 close_stream 可能将 status 置为 closed，
        # 导致事件写入已关闭的 stream。当前不修复，原因：
        # 1. close_stream 和 push_event 在同一执行流中由 controller 顺序调用，close 一定在最后一个 push 之后
        # 2. 即使未来引入外部强制关闭，孤儿事件有 TTL 自动清理
        # 3. events 已通过 _persist_events 持久化到 DB，stream 只是 SSE 传输通道
        status = await self._redis.hget(meta_key, "status")
        if status is None or status == "closed":
            return False

        stream_key = self._stream_key(stream_id)
        event_json = json.dumps(event, ensure_ascii=False, default=str)
        event_type = event.get("type", "")

        # XADD + 首推 EXPIRE 原子合一（单 key Lua）。脚本内 TTL==-1 判据负责首推
        # 检测，省去单独的 exists 预查，并消除 XADD 与 EXPIRE 之间的孤儿窗口。
        # best-effort 契约：stream key 必带 TTL（= EXECUTION_TIMEOUT + STREAM_TTL_GRACE，
        # 覆盖 post-processing）；与 meta_key 剩余 TTL 的精确对齐留给 PR-C（届时
        # create_stream / TTL bump 移到 RUNNING，时钟起点统一）。
        entry_id = await self._script_xadd_with_ttl(
            keys=[stream_key],
            args=[event_type, event_json, self._stream_ttl],
        )

        # 注入 _stream_id 到原始 event dict（供 SSE 层使用）
        event["_stream_id"] = entry_id

        return True

    async def consume_events(
        self,
        stream_id: str,
        heartbeat_interval: Optional[float] = None,
        user_id: Optional[str] = None,
        last_event_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        meta_key = self._meta_key(stream_id)
        stream_key = self._stream_key(stream_id)

        # 校验 stream 存在
        status = await self._redis.hget(meta_key, "status")
        if status is None:
            raise StreamNotFoundError(stream_id)

        # 校验 owner
        owner = await self._redis.hget(meta_key, "owner")
        if owner and user_id and owner != user_id:
            raise StreamNotFoundError(stream_id)

        # 生成 consumer_id，标记为 streaming
        # 不移除 TTL — stream 生命周期由 stream TTL（EXECUTION_TIMEOUT+STREAM_TTL_GRACE）决定，
        # 如果 producer crash 未调 close_stream，不会产生永久孤儿 key
        consumer_id = os.urandom(8).hex()
        await self._redis.hset(meta_key, mapping={
            "status": "streaming",
            "consumer_id": consumer_id,
        })

        # 起始 ID
        cursor = last_event_id if last_event_id else "0-0"
        block_ms = int((heartbeat_interval or 15) * 1000)

        retry_count = 0
        try:
            while True:
                # XREAD BLOCK
                try:
                    result = await self._redis.xread(
                        {stream_key: cursor}, count=100, block=block_ms
                    )
                    retry_count = 0  # 成功重置
                except (aioredis.ConnectionError, aioredis.TimeoutError):
                    retry_count += 1
                    if retry_count > 2:
                        logger.error(
                            f"Redis connection lost during consume {stream_id}, "
                            f"giving up after {retry_count} retries"
                        )
                        break
                    logger.warning(
                        f"Redis connection lost during consume {stream_id}, "
                        f"retry {retry_count}/2"
                    )
                    await asyncio.sleep(10)  # failover 窗口 15-20s，sleep 10s × 2 覆盖
                    continue

                if not result:
                    # 检查 producer lease 是否仍然存活
                    lease_meta = await self._redis.hmget(
                        meta_key, "lease_check_key", "lease_expected_owner"
                    )
                    lease_key, expected_owner = lease_meta[0], lease_meta[1]
                    if lease_key and expected_owner:
                        lease_val = await self._redis.get(lease_key)
                        if lease_val is None or lease_val != expected_owner:
                            # Lease 过期或已被新执行接管 → producer 已不在
                            logger.warning(
                                f"Lease lost for stream {stream_id} "
                                f"(key={lease_key}, expected={expected_owner}, "
                                f"actual={lease_val}) — closing consumer"
                            )
                            yield {
                                "type": "error",
                                "data": {
                                    "success": False,
                                    "error": "Execution lease expired (producer lost)",
                                    # transport 自产的 error 不经 sanitize_error_event,
                                    # 这里直接注入 consumer(SSE GET)的 req-id 当定位码。
                                    "request_id": get_request_id() or None,
                                },
                            }
                            return
                    # 超时 → 发送心跳
                    yield {"type": "__ping__"}
                    continue

                # result: [[stream_key, [(entry_id, fields), ...]]]
                for _key, entries in result:
                    for entry_id, fields in entries:
                        cursor = entry_id
                        event_json = fields.get("data", "{}")
                        event = json.loads(event_json)
                        event["_stream_id"] = entry_id
                        yield event

                        # 终结事件 → 正常退出（producer 负责 close_stream）
                        if event.get("type") in _TERMINAL_EVENTS:
                            return
        finally:
            # Consumer 断连：CAS 回退 meta 到 pending（单 key Lua，Cluster 安全）。
            # 仅当 consumer_id 仍匹配（说明没有新 consumer 接管）时才回退，
            # 避免覆盖新 consumer 的 streaming 或 producer 的 closed。
            # 不缩短 TTL — stream 生命周期由 stream TTL（EXECUTION_TIMEOUT+STREAM_TTL_GRACE）决定。
            # 注意：break 退出 async for 不会自动触发 aclose()，调用方需显式
            # await gen.aclose() 以确保此 finally 块执行。
            await self._script_revert_to_pending(
                keys=[meta_key],
                args=[consumer_id],
            )

    async def close_stream(self, stream_id: str) -> bool:
        meta_key = self._meta_key(stream_id)
        status = await self._redis.hget(meta_key, "status")
        if status is None:
            return False

        # 标记为 closed + 设置延迟清理 TTL
        cleanup_ttl = max(self._cleanup_ttl, 10)
        pipe = self._redis.pipeline(transaction=False)
        pipe.hset(meta_key, "status", "closed")
        pipe.expire(meta_key, cleanup_ttl)
        pipe.expire(self._stream_key(stream_id), cleanup_ttl)
        await pipe.execute()

        logger.debug(f"Redis stream {stream_id} closed (TTL={cleanup_ttl}s)")
        return True

    async def get_stream_status(self, stream_id: str) -> Optional[str]:
        return await self._redis.hget(self._meta_key(stream_id), "status")

    async def is_stream_alive(self, stream_id: str) -> bool:
        """Check if the stream meta key still exists in Redis (not expired)."""
        status = await self._redis.hget(self._meta_key(stream_id), "status")
        return status is not None and status != "closed"

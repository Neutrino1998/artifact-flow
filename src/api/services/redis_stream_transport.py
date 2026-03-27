"""
RedisStreamTransport — Redis Streams-backed StreamTransport 实现

支持跨 Worker 的事件 push/consume。
使用 Redis Streams (XADD/XREAD) 实现事件缓冲和消费。

Key 设计：
    stream:{msg_id}       STREAM    TTL=STREAM_TTL   事件流
    stream_meta:{msg_id}  HASH      TTL=STREAM_TTL   stream 元数据 {owner, status}
"""

import json
from typing import Any, AsyncGenerator, Dict, Literal, Optional

import redis.asyncio as aioredis

from api.services.stream_transport import StreamAlreadyExistsError, StreamNotFoundError
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# 终结事件类型
_TERMINAL_EVENTS = frozenset(("complete", "cancelled", "error"))


class RedisStreamTransport:
    """Redis Streams-backed StreamTransport 实现"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        stream_ttl: int = 30,
        stream_timeout: int = 1800,
    ):
        self._redis = redis_client
        self._stream_ttl = stream_ttl
        self._stream_timeout = stream_timeout

    # ── Key helpers ──

    @staticmethod
    def _stream_key(stream_id: str) -> str:
        return f"stream:{stream_id}"

    @staticmethod
    def _meta_key(stream_id: str) -> str:
        return f"stream_meta:{stream_id}"

    # ── Protocol methods ──

    async def create_stream(self, stream_id: str, owner_user_id: Optional[str] = None) -> None:
        meta_key = self._meta_key(stream_id)

        # 检查是否已存在且未关闭
        existing_status = await self._redis.hget(meta_key, "status")
        if existing_status is not None and existing_status != "closed":
            raise StreamAlreadyExistsError(stream_id)

        # 创建元数据
        await self._redis.hset(meta_key, mapping={
            "owner": owner_user_id or "",
            "status": "pending",
        })
        await self._redis.expire(meta_key, self._stream_ttl)
        # stream key 的 TTL 也设置（如果前端一直不连接，自动清理）
        stream_key = self._stream_key(stream_id)
        await self._redis.expire(stream_key, self._stream_ttl)

        logger.debug(f"Created Redis stream: {stream_id}")

    async def push_event(self, stream_id: str, event: Dict[str, Any]) -> bool:
        meta_key = self._meta_key(stream_id)
        status = await self._redis.hget(meta_key, "status")
        if status is None or status == "closed":
            return False

        stream_key = self._stream_key(stream_id)
        event_json = json.dumps(event, ensure_ascii=False, default=str)
        event_type = event.get("type", "")

        # XADD with MAXLEN ~ 1000（近似修剪，性能更好）
        entry_id = await self._redis.xadd(
            stream_key,
            {"type": event_type, "data": event_json},
            maxlen=1000,
            approximate=True,
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

        # 标记为 streaming + 移除 TTL（前端已连接）
        await self._redis.hset(meta_key, "status", "streaming")
        await self._redis.persist(meta_key)
        await self._redis.persist(stream_key)

        # 起始 ID
        cursor = last_event_id if last_event_id else "0-0"
        block_ms = int((heartbeat_interval or 15) * 1000)

        try:
            while True:
                # XREAD BLOCK
                result = await self._redis.xread(
                    {stream_key: cursor}, count=100, block=block_ms
                )

                if not result:
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

                        # 终结事件 → 退出
                        if event.get("type") in _TERMINAL_EVENTS:
                            return
        finally:
            await self.close_stream(stream_id)

    async def close_stream(self, stream_id: str) -> bool:
        meta_key = self._meta_key(stream_id)
        status = await self._redis.hget(meta_key, "status")
        if status is None:
            return False

        # 标记为 closed + 设置延迟清理 TTL
        cleanup_ttl = max(self._stream_ttl, 10)
        pipe = self._redis.pipeline(transaction=False)
        pipe.hset(meta_key, "status", "closed")
        pipe.expire(meta_key, cleanup_ttl)
        pipe.expire(self._stream_key(stream_id), cleanup_ttl)
        await pipe.execute()

        logger.debug(f"Redis stream {stream_id} closed (TTL={cleanup_ttl}s)")
        return True

    def get_stream_status(self, stream_id: str) -> Optional[str]:
        # Protocol 定义为同步方法 — Redis 实现无法同步读取
        # 返回 None，调用方应使用 async 版本
        return None

    async def get_stream_status_async(self, stream_id: str) -> Optional[str]:
        """异步版本的 get_stream_status"""
        return await self._redis.hget(self._meta_key(stream_id), "status")

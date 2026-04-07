"""
StreamTransport Protocol + InMemoryStreamTransport 实现

定义事件推送/消费的传输层接口，不包含执行语义（如 cancelled）。
Protocol + InMemory 实现同文件（与 runtime_store.py 对称）。
未来可替换为 Redis Streams 实现。
"""

import asyncio
from typing import Protocol, runtime_checkable, Optional, Dict, Any, AsyncGenerator, Literal
from datetime import datetime
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class StreamNotFoundError(Exception):
    """Stream 不存在异常"""
    def __init__(self, message_id: str):
        self.message_id = message_id
        super().__init__(f"Stream '{message_id}' not found")


class StreamAlreadyExistsError(Exception):
    """Stream 已存在异常"""
    def __init__(self, message_id: str):
        self.message_id = message_id
        super().__init__(f"Stream '{message_id}' already exists")


@runtime_checkable
class StreamTransport(Protocol):
    """流式事件传输协议"""

    async def create_stream(self, stream_id: str, owner_user_id: Optional[str] = None) -> None: ...
    async def push_event(self, stream_id: str, event: Dict[str, Any]) -> bool: ...
    async def consume_events(
        self,
        stream_id: str,
        heartbeat_interval: Optional[float] = None,
        user_id: Optional[str] = None,
        last_event_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]: ...
    async def close_stream(self, stream_id: str) -> bool: ...
    async def get_stream_status(self, stream_id: str) -> Optional[str]: ...
    async def is_stream_alive(self, stream_id: str) -> bool: ...


# ============================================================
# InMemoryStreamTransport — 基于内存的事件缓冲队列实现
# ============================================================


@dataclass
class StreamContext:
    """
    单个 stream 的上下文

    Attributes:
        queue: 事件缓冲队列
        created_at: 创建时间
        status: 状态 (pending: 等待连接, streaming: 正在推送, closed: 已关闭)
        ttl_task: TTL 清理任务
        cancelled: 取消事件，用于通知后台任务停止
        owner_user_id: 创建此 stream 的用户 ID（用于消费时校验）
    """
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: datetime = field(default_factory=datetime.now)
    status: Literal["pending", "streaming", "closed"] = "pending"
    ttl_task: Optional[asyncio.Task] = None
    cleanup_task: Optional[asyncio.Task] = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    owner_user_id: Optional[str] = None


class InMemoryStreamTransport:
    """
    基于内存的事件缓冲队列管理器

    职责：
    - 为每个 message_id 创建独立的事件队列
    - 在 POST /chat 时创建队列，开始缓冲事件
    - 在 GET /stream 时消费队列，通过 SSE 推送
    - TTL 机制防止内存泄漏
    - 提供取消机制，通知后台任务在 stream 关闭时停止
    """

    def __init__(self, ttl_seconds: int = 30):
        self.streams: Dict[str, StreamContext] = {}
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._closed_streams: set = set()

        logger.info(f"InMemoryStreamTransport initialized (TTL: {ttl_seconds}s)")

    async def create_stream(
        self, message_id: str, owner_user_id: Optional[str] = None
    ) -> StreamContext:
        async with self._lock:
            existing = self.streams.get(message_id)
            if existing:
                if existing.status == "closed":
                    if existing.cleanup_task:
                        existing.cleanup_task.cancel()
                    del self.streams[message_id]
                else:
                    raise StreamAlreadyExistsError(message_id)

            self._closed_streams.discard(message_id)

            context = StreamContext(owner_user_id=owner_user_id)
            self.streams[message_id] = context

            context.ttl_task = asyncio.create_task(
                self._ttl_cleanup(message_id)
            )

            logger.debug(f"Created stream: {message_id}")
            return context

    async def _ttl_cleanup(self, message_id: str) -> None:
        await asyncio.sleep(self.ttl_seconds)

        async with self._lock:
            context = self.streams.get(message_id)
            if context and context.status == "pending":
                logger.warning(f"Stream {message_id} expired (TTL={self.ttl_seconds}s, status=pending)")
                await self._close_stream_internal(message_id)

    async def push_event(self, message_id: str, event: Dict[str, Any]) -> bool:
        context = self.streams.get(message_id)
        if not context or context.status == "closed":
            if message_id not in self._closed_streams:
                self._closed_streams.add(message_id)
                logger.warning(f"Stream {message_id} closed, subsequent push_event calls will be ignored")
            return False

        await context.queue.put(event)
        return True

    async def consume_events(
        self,
        message_id: str,
        heartbeat_interval: Optional[float] = None,
        user_id: Optional[str] = None,
        last_event_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        async with self._lock:
            context = self.streams.get(message_id)
            if not context:
                raise StreamNotFoundError(message_id)

            if context.owner_user_id and user_id and context.owner_user_id != user_id:
                raise StreamNotFoundError(message_id)

            if context.ttl_task:
                context.ttl_task.cancel()
                context.ttl_task = None

            context.status = "streaming"
            logger.debug(f"Stream {message_id} started consuming")

        try:
            while True:
                if heartbeat_interval is not None:
                    try:
                        event = await asyncio.wait_for(
                            context.queue.get(), timeout=heartbeat_interval
                        )
                    except asyncio.TimeoutError:
                        yield {"type": "__ping__"}
                        continue
                else:
                    event = await context.queue.get()

                yield event

                event_type = event.get("type", "")
                if event_type in ("complete", "cancelled", "error"):
                    logger.debug(f"Stream {message_id} received terminal event: {event_type}")
                    break
        finally:
            await self.close_stream(message_id)

    async def close_stream(self, message_id: str) -> bool:
        async with self._lock:
            return await self._close_stream_internal(message_id)

    async def _close_stream_internal(self, message_id: str) -> bool:
        context = self.streams.get(message_id)
        if not context:
            return False

        context.status = "closed"
        context.cancelled.set()

        if context.ttl_task:
            context.ttl_task.cancel()
            context.ttl_task = None

        context.cleanup_task = asyncio.create_task(
            self._delayed_cleanup(message_id, delay=5.0)
        )

        logger.debug(f"Stream {message_id} closed (delayed cleanup scheduled)")
        return True

    async def _delayed_cleanup(self, message_id: str, delay: float = 5.0) -> None:
        await asyncio.sleep(delay)

        async with self._lock:
            context = self.streams.get(message_id)
            if context and context.status == "closed":
                del self.streams[message_id]
                self._closed_streams.discard(message_id)
                logger.debug(f"Stream {message_id} cleaned up from memory")

    async def get_stream_status(self, message_id: str) -> Optional[str]:
        context = self.streams.get(message_id)
        return context.status if context else None

    async def is_stream_alive(self, message_id: str) -> bool:
        """Check if stream exists and is not closed."""
        context = self.streams.get(message_id)
        return context is not None and context.status != "closed"

    @property
    def active_stream_count(self) -> int:
        return len(self.streams)

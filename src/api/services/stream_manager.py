"""
StreamManager - 事件缓冲队列管理

解决的问题：
POST /chat 启动任务后，Graph 可能在前端 SSE 连接建立之前
就已经开始产生事件，导致 metadata / start 等早期事件丢失。

架构设计：
    ┌───────────────────────────────────────────────────────┐
    │  streams: Dict[thread_id, StreamContext]               │
    │                                                        │
    │  StreamContext:                                        │
    │    - queue: asyncio.Queue[Dict]                       │
    │    - created_at: datetime                             │
    │    - status: pending | streaming | closed             │
    │    - ttl_task: asyncio.Task (自动清理)                 │
    └───────────────────────────────────────────────────────┘

交互时序：
    POST /chat                          GET /stream/{thread_id}
        │                                      │
        ▼                                      │
    [创建 StreamContext]                       │
    [启动 TTL 定时器 (30s)]                    │
        │                                      │
        ▼                                      ▼
    [push 事件到队列]  ──────────────► [消费并推送 SSE]
        │                                      │
        ▼                                      ▼
    [push complete 事件]      ────────► [推送后关闭连接]
"""

import asyncio
from typing import Dict, Optional, AsyncGenerator, Literal, Any
from datetime import datetime
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class StreamNotFoundError(Exception):
    """Stream 不存在异常"""
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        super().__init__(f"Stream '{thread_id}' not found")


class StreamAlreadyExistsError(Exception):
    """Stream 已存在异常"""
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        super().__init__(f"Stream '{thread_id}' already exists")


@dataclass
class StreamContext:
    """
    单个 stream 的上下文

    Attributes:
        queue: 事件缓冲队列
        created_at: 创建时间
        status: 状态 (pending: 等待连接, streaming: 正在推送, closed: 已关闭)
        ttl_task: TTL 清理任务
    """
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: datetime = field(default_factory=datetime.now)
    status: Literal["pending", "streaming", "closed"] = "pending"
    ttl_task: Optional[asyncio.Task] = None


class StreamManager:
    """
    事件缓冲队列管理器

    职责：
    - 为每个 thread_id 创建独立的事件队列
    - 在 POST /chat 时创建队列，开始缓冲事件
    - 在 GET /stream 时消费队列，通过 SSE 推送
    - TTL 机制防止内存泄漏

    使用方式：
        # POST /chat 处理器
        context = stream_manager.create_stream(thread_id)
        asyncio.create_task(run_graph_and_push_events(thread_id))

        # 后台任务
        async def run_graph_and_push_events(thread_id):
            async for event in controller.stream_execute(...):
                stream_manager.push_event(thread_id, event)

        # GET /stream 处理器
        async for event in stream_manager.consume_events(thread_id):
            yield f"data: {json.dumps(event)}\\n\\n"
    """

    def __init__(self, ttl_seconds: int = 30):
        """
        初始化 StreamManager

        Args:
            ttl_seconds: 队列 TTL（秒），前端未连接时自动清理
        """
        self.streams: Dict[str, StreamContext] = {}
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

        logger.info(f"StreamManager initialized (TTL: {ttl_seconds}s)")

    async def create_stream(self, thread_id: str) -> StreamContext:
        """
        创建事件队列，并启动 TTL 定时器

        Args:
            thread_id: LangGraph 线程 ID

        Returns:
            StreamContext 实例

        Raises:
            StreamAlreadyExistsError: 如果 stream 已存在
        """
        async with self._lock:
            if thread_id in self.streams:
                raise StreamAlreadyExistsError(thread_id)

            context = StreamContext()
            self.streams[thread_id] = context

            # 启动 TTL 定时器
            context.ttl_task = asyncio.create_task(
                self._ttl_cleanup(thread_id)
            )

            logger.debug(f"Created stream: {thread_id}")
            return context

    async def _ttl_cleanup(self, thread_id: str) -> None:
        """
        TTL 到期后自动清理队列（防止内存泄漏）

        Args:
            thread_id: 线程 ID
        """
        await asyncio.sleep(self.ttl_seconds)

        async with self._lock:
            context = self.streams.get(thread_id)
            if context and context.status == "pending":
                # 前端未连接，清理队列
                logger.warning(f"Stream {thread_id} expired (TTL={self.ttl_seconds}s, status=pending)")
                await self._close_stream_internal(thread_id)

    async def push_event(self, thread_id: str, event: Dict[str, Any]) -> bool:
        """
        推送事件到队列

        Args:
            thread_id: 线程 ID
            event: 事件字典

        Returns:
            是否成功推送
        """
        context = self.streams.get(thread_id)
        if not context or context.status == "closed":
            logger.warning(f"Cannot push event to stream {thread_id}: stream not found or closed")
            return False

        await context.queue.put(event)
        return True

    async def consume_events(self, thread_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        消费事件（前端 SSE 连接时调用）

        从队列中取出事件并 yield。遇到终结事件（complete/error）后退出。

        Args:
            thread_id: 线程 ID

        Yields:
            事件字典

        Raises:
            StreamNotFoundError: 如果 stream 不存在
        """
        async with self._lock:
            context = self.streams.get(thread_id)
            if not context:
                raise StreamNotFoundError(thread_id)

            # 取消 TTL 定时器（前端已连接）
            if context.ttl_task:
                context.ttl_task.cancel()
                context.ttl_task = None

            context.status = "streaming"
            logger.debug(f"Stream {thread_id} started consuming")

        try:
            while True:
                event = await context.queue.get()
                yield event

                # 终结事件后退出
                event_type = event.get("type", "")
                if event_type in ("complete", "error"):
                    logger.debug(f"Stream {thread_id} received terminal event: {event_type}")
                    break
        finally:
            await self.close_stream(thread_id)

    async def close_stream(self, thread_id: str) -> bool:
        """
        关闭并清理 stream

        Args:
            thread_id: 线程 ID

        Returns:
            是否成功关闭
        """
        async with self._lock:
            return await self._close_stream_internal(thread_id)

    async def _close_stream_internal(self, thread_id: str) -> bool:
        """
        内部关闭方法（需要在锁内调用）

        Args:
            thread_id: 线程 ID

        Returns:
            是否成功关闭
        """
        context = self.streams.get(thread_id)
        if not context:
            return False

        context.status = "closed"

        # 取消 TTL 任务
        if context.ttl_task:
            context.ttl_task.cancel()
            context.ttl_task = None

        # 从字典中移除
        del self.streams[thread_id]
        logger.debug(f"Stream {thread_id} closed")

        return True

    def get_stream_status(self, thread_id: str) -> Optional[str]:
        """
        获取 stream 状态

        Args:
            thread_id: 线程 ID

        Returns:
            状态字符串或 None
        """
        context = self.streams.get(thread_id)
        return context.status if context else None

    @property
    def active_stream_count(self) -> int:
        """获取活跃 stream 数量"""
        return len(self.streams)

"""
StreamTransport Protocol + InMemoryStreamTransport 实现

定义事件推送/消费的传输层接口，不包含执行语义（如 cancelled）。
Protocol + InMemory 实现同文件（与 runtime_store.py 对称）。
RedisStreamTransport 是兄弟实现（多 worker / 持久化）。
"""

import asyncio
from typing import Protocol, runtime_checkable, Optional, Dict, Any, AsyncGenerator, Literal, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# Mirror RedisStreamTransport's XADD MAXLEN ~ 1000 buffer cap.
# Bounds memory while still letting a reconnecting consumer replay enough
# history to rebuild the UI for any normal turn.
DEFAULT_MAX_HISTORY = 1000

# Event types that terminate the stream (consumer should exit after yielding).
_TERMINAL_EVENTS = ("complete", "cancelled", "error")


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

    async def create_stream(self, stream_id: str, owner_user_id: Optional[str] = None, lease_check_key: Optional[str] = None, lease_expected_owner: Optional[str] = None) -> None: ...
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

    history + new_event 取代了原来的 asyncio.Queue 模型：
    - push 把事件 append 到 history（已带分配的 _stream_id），并 set new_event。
    - consume 用 cursor 从 history 拉取所有 id > cursor 的事件，然后 wait
      new_event 等下一批。

    这样新接入的 consumer（reconnect / 切回 conversation）可以从 0 重放
    整轮事件，与 RedisStreamTransport 的 XREAD `0-0` 行为对齐。

    Attributes:
        history: (event_id, event) 列表，append-only，超过 max_history
                 后从头部丢弃（与 Redis MAXLEN 语义一致）。
        next_id: 单调递增的事件 id 计数器。
        new_event: 新事件信号；push 后 set，consume 在 drain 干净后 wait。
        created_at: 创建时间。
        status: pending(等待连接) / streaming(正在推送) / closed(已关闭)。
        ttl_task: TTL 清理任务。
        cancelled: 取消事件，通知 consumer 退出。
        owner_user_id: 创建此 stream 的用户 ID（消费时校验）。
    """
    history: List[Tuple[int, Dict[str, Any]]] = field(default_factory=list)
    next_id: int = 0
    new_event: asyncio.Event = field(default_factory=asyncio.Event)
    created_at: datetime = field(default_factory=datetime.now)
    status: Literal["pending", "streaming", "closed"] = "pending"
    ttl_task: Optional[asyncio.Task] = None
    cleanup_task: Optional[asyncio.Task] = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    owner_user_id: Optional[str] = None


class InMemoryStreamTransport:
    """
    基于内存的事件缓冲实现（history list + asyncio.Event 信号）。

    职责：
    - 为每个 message_id 维护一个有界的事件历史 buffer。
    - POST /chat 时 create_stream，开始缓冲事件。
    - GET /stream 时 consume_events，按 last_event_id 重放或从头开始。
    - TTL / cancelled / owner 校验语义与 RedisStreamTransport 对齐，
      使 dev (InMemory) 与 prod (Redis) 行为一致。
    """

    def __init__(self, ttl_seconds: int = 30, max_history: int = DEFAULT_MAX_HISTORY):
        self.streams: Dict[str, StreamContext] = {}
        self.ttl_seconds = ttl_seconds
        self.max_history = max_history
        self._lock = asyncio.Lock()
        self._closed_streams: set = set()

        logger.info(
            f"InMemoryStreamTransport initialized "
            f"(TTL: {ttl_seconds}s, max_history: {max_history})"
        )

    async def create_stream(
        self, message_id: str, owner_user_id: Optional[str] = None, lease_check_key: Optional[str] = None, lease_expected_owner: Optional[str] = None,
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

        # Assign monotonic id and inject into the event dict so the SSE router
        # can emit it as the SSE `id:` field. Mirrors RedisStreamTransport's
        # post-XADD `event["_stream_id"] = entry_id` behavior.
        eid = context.next_id
        context.next_id += 1
        event["_stream_id"] = str(eid)

        # Append to bounded history. Trim from head once full (Redis MAXLEN
        # ~ 1000 equivalent). A consumer whose cursor is older than the
        # oldest retained entry will silently miss those events — same loss
        # model as Redis; for normal turns 1000 events is more than enough.
        context.history.append((eid, event))
        if len(context.history) > self.max_history:
            context.history.pop(0)

        # Wake any consumer waiting on new events. set() is idempotent.
        context.new_event.set()
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

        # Cursor: only events with id > cursor are yielded. -1 means "from
        # the very start", so an absent Last-Event-ID replays everything
        # still in history (matches Redis cursor "0-0" semantics).
        try:
            cursor = int(last_event_id) if last_event_id else -1
        except ValueError:
            cursor = -1

        try:
            while True:
                # Drain everything past cursor in a single pass. Snapshot via
                # list() so that concurrent push_event mutations of history
                # don't disturb iteration; we'll see new entries on the next
                # outer loop iteration.
                #
                # NOTE: cancelled is intentionally NOT checked inside this
                # drain. Producer calls close_stream() AFTER pushing the
                # terminal event, so cancelled is set on every normal close.
                # Terminating mid-drain on cancelled would mean a consumer
                # connecting AFTER close (history fully populated) would
                # only emit the first event and bail. The terminal-event
                # detection below provides the right exit condition for
                # normally-closed streams; cancelled-without-terminal is
                # only checked when we run out of history to drain.
                drained_any = False
                for eid, event in list(context.history):
                    if eid > cursor:
                        cursor = eid
                        drained_any = True
                        # Shallow copy so the SSE router's `event.pop("_stream_id")`
                        # (and any other consumer mutation) doesn't poison the
                        # buffered history. Otherwise a later replaying consumer
                        # would see the same dict with _stream_id missing, and
                        # the SSE response would drop its `id:` field for that
                        # event — breaking Last-Event-ID continuity. Redis
                        # avoids this naturally because each XREAD round-trips
                        # through JSON; InMemory must copy explicitly.
                        yield dict(event)
                        if event.get("type", "") in _TERMINAL_EVENTS:
                            logger.debug(
                                f"Stream {message_id} received terminal event: {event['type']}"
                            )
                            return

                # Drain exhausted. If the stream was force-closed without a
                # terminal event ever being pushed (rare — usually shutdown),
                # cancelled tells us to stop waiting.
                if context.cancelled.is_set():
                    logger.debug(
                        f"Stream {message_id} cancelled with no terminal event — exiting"
                    )
                    return

                # Nothing new past cursor — wait for next push or heartbeat.
                # clear() before the re-check guards against the push that
                # raced between our drain and clear: if it ran, we re-enter
                # the loop and drain immediately; if it hasn't, our wait
                # will be woken by the eventual set().
                context.new_event.clear()
                if any(eid > cursor for eid, _ in context.history):
                    continue

                if heartbeat_interval is not None:
                    try:
                        await asyncio.wait_for(
                            context.new_event.wait(), timeout=heartbeat_interval
                        )
                    except asyncio.TimeoutError:
                        if context.cancelled.is_set():
                            return
                        # Don't emit ping if we just drained events — the
                        # heartbeat is for *idle* periods, not back-to-back
                        # with data.
                        if not drained_any:
                            yield {"type": "__ping__"}
                        continue
                else:
                    await context.new_event.wait()
        finally:
            # Consumer 断连：回退到 pending（与 RedisStreamTransport 语义对齐）。
            # close_stream() 由 producer 在执行结束后调用，consumer 不应关闭 stream。
            # 重新启动 TTL 防止 producer 挂掉后 stream 永久驻留内存。
            async with self._lock:
                context = self.streams.get(message_id)
                if context and context.status == "streaming":
                    context.status = "pending"
                    context.ttl_task = asyncio.create_task(
                        self._ttl_cleanup(message_id)
                    )
                    logger.debug(f"Stream {message_id} reverted to pending (consumer disconnect)")

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

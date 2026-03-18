"""
TaskManager - Background Task 生命周期管理 + Interrupt + Message Queue

职责：
- 持有任务引用，防止 GC 回收
- Semaphore 限制并发数
- Graceful shutdown 支持
- Interrupt 管理（asyncio.Event 暂停/恢复执行）
- 去重（message_id 天然唯一，重复提交返回 409）
- 执行中消息注入（message queue）
"""

import asyncio
from dataclasses import dataclass, field
from typing import Coroutine, Literal, Optional, Dict, Any, List

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class DuplicateExecutionError(Exception):
    """重复执行错误（message_id 已存在活跃任务）"""
    pass


@dataclass
class InterruptState:
    """中断状态"""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    interrupt_data: Dict[str, Any] = field(default_factory=dict)  # 发给前端的中断信息
    resume_data: Optional[Dict[str, Any]] = None  # 用户确认结果


class TaskManager:
    """
    管理执行的后台任务

    职责：
    - 持有任务引用，防止 GC 回收
    - Semaphore 限制并发数
    - Graceful shutdown 支持
    - Interrupt 管理（用 message_id 定位执行）
    - 去重（message_id 天然唯一）
    - Message Queue（执行中消息注入）
    """

    def __init__(self, max_concurrent: int = 10):
        self._tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent

        # Interrupt 管理 — key = message_id
        self._interrupts: dict[str, InterruptState] = {}

        # Cancellation 管理 — key = message_id
        self._cancellations: dict[str, asyncio.Event] = {}

        # Message Queue — key = message_id
        self._queues: dict[str, asyncio.Queue] = {}

        # Conversation → active message_id 映射（用于消息注入）
        self._active_conversations: dict[str, str] = {}

        logger.info(f"TaskManager initialized (max_concurrent={max_concurrent})")

    # ========================================
    # 任务提交
    # ========================================

    async def submit(self, task_id: str, coro: Coroutine) -> asyncio.Task:
        """
        提交一个后台任务

        用 semaphore 包裹以限制并发，持有引用防 GC，完成后自动清理。
        message_id 天然唯一，重复提交抛 DuplicateExecutionError。

        Args:
            task_id: 任务 ID（message_id）
            coro: 要执行的协程

        Returns:
            asyncio.Task 实例

        Raises:
            DuplicateExecutionError: task_id 已存在活跃任务
        """
        if task_id in self._tasks:
            raise DuplicateExecutionError(f"Execution already running for {task_id}")

        async def _wrapped():
            async with self._semaphore:
                try:
                    await coro
                except Exception:
                    logger.exception(f"Task {task_id} failed with unhandled exception")
                finally:
                    self._tasks.pop(task_id, None)
                    self._interrupts.pop(task_id, None)
                    self._cancellations.pop(task_id, None)
                    self._queues.pop(task_id, None)
                    self._active_conversations = {
                        k: v for k, v in self._active_conversations.items() if v != task_id
                    }
                    logger.debug(f"Task {task_id} completed and cleaned up (active: {len(self._tasks)})")

        task = asyncio.create_task(_wrapped(), name=f"exec-{task_id}")
        self._tasks[task_id] = task
        logger.info(f"Task {task_id} submitted (active: {len(self._tasks)})")
        return task

    # ========================================
    # Interrupt 管理
    # ========================================

    def create_interrupt(self, message_id: str, data: Dict[str, Any]) -> InterruptState:
        """
        创建一个中断状态

        Args:
            message_id: 执行标识
            data: 发送给前端的中断信息（tool, params, execution_context）

        Returns:
            InterruptState 实例
        """
        interrupt = InterruptState(interrupt_data=data)
        self._interrupts[message_id] = interrupt
        logger.info(f"Interrupt created for {message_id}")
        return interrupt

    async def resolve_interrupt(
        self, message_id: str, resume_data: Dict[str, Any]
    ) -> Literal["resolved", "not_found", "already_resolved"]:
        """
        解决中断（用户确认后调用）

        Args:
            message_id: 执行标识
            resume_data: 用户确认结果（approved, always_allow 等）

        Returns:
            "resolved": 成功唤醒
            "not_found": 找不到中断
            "already_resolved": 中断已被处理过
        """
        interrupt = self._interrupts.get(message_id)
        if not interrupt:
            logger.warning(f"No interrupt found for {message_id}")
            return "not_found"

        if interrupt.event.is_set():
            logger.warning(f"Interrupt for {message_id} already resolved")
            return "already_resolved"

        interrupt.resume_data = resume_data
        interrupt.event.set()
        logger.info(f"Interrupt resolved for {message_id}: {resume_data}")
        return "resolved"

    def get_interrupt(self, message_id: str) -> Optional[InterruptState]:
        """获取中断状态"""
        return self._interrupts.get(message_id)

    # ========================================
    # Cancellation 管理
    # ========================================

    def request_cancel(self, message_id: str) -> bool:
        """
        请求取消执行。

        Returns:
            True — 请求已发出
            False — 没有活跃任务
        """
        if message_id not in self._tasks:
            return False
        if message_id not in self._cancellations:
            self._cancellations[message_id] = asyncio.Event()
        self._cancellations[message_id].set()
        # 同时唤醒可能阻塞的 interrupt，使其不阻碍退出
        interrupt = self._interrupts.get(message_id)
        if interrupt and not interrupt.event.is_set():
            interrupt.resume_data = {"approved": False, "reason": "cancelled"}
            interrupt.event.set()
        logger.info(f"Cancellation requested for {message_id}")
        return True

    def is_cancelled(self, message_id: str) -> bool:
        """检查执行是否已被取消"""
        event = self._cancellations.get(message_id)
        return event.is_set() if event else False

    # ========================================
    # Conversation → Active Execution 映射
    # ========================================

    def try_reserve_conversation(self, conversation_id: str, message_id: str) -> Optional[str]:
        """
        原子地检查并注册 conversation 的活跃执行。

        Reservation 一旦写入即视为有效占位，直到被 unregister_conversation
        或 task cleanup 显式清除。不检查 _tasks — 因为 reservation 发生在
        submit 之前，此时任务尚未进入 _tasks。

        Returns:
            None — 预留成功
            str  — 已有预留的 message_id（预留失败）
        """
        existing = self._active_conversations.get(conversation_id)
        if existing:
            return existing
        self._active_conversations[conversation_id] = message_id
        return None

    def unregister_conversation(self, conversation_id: str) -> None:
        """显式取消 conversation 的活跃执行映射"""
        self._active_conversations.pop(conversation_id, None)

    def get_active_message_id(self, conversation_id: str) -> Optional[str]:
        """获取 conversation 当前活跃的 message_id，无活跃任务返回 None"""
        message_id = self._active_conversations.get(conversation_id)
        if message_id and message_id in self._tasks:
            return message_id
        self._active_conversations.pop(conversation_id, None)
        return None

    # ========================================
    # Message Queue（执行中消息注入）
    # ========================================

    def inject_message(self, message_id: str, content: str) -> None:
        """
        向执行中的任务注入消息

        Args:
            message_id: 执行标识
            content: 消息内容
        """
        if message_id not in self._queues:
            self._queues[message_id] = asyncio.Queue()
        self._queues[message_id].put_nowait(content)
        logger.debug(f"Message injected for {message_id}")

    def drain_messages(self, message_id: str) -> List[str]:
        """
        非阻塞地取出所有排队消息

        Args:
            message_id: 执行标识

        Returns:
            消息列表（可能为空）
        """
        queue = self._queues.get(message_id)
        if not queue:
            return []

        messages = []
        while not queue.empty():
            try:
                messages.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    # ========================================
    # Shutdown
    # ========================================

    async def shutdown(self, timeout: float = 30.0):
        """
        Graceful shutdown：等待所有运行中任务完成，超时后 cancel 剩余任务

        Args:
            timeout: 等待超时时间（秒）
        """
        if not self._tasks:
            logger.info("TaskManager shutdown: no active tasks")
            return

        task_count = len(self._tasks)
        logger.info(f"TaskManager shutdown: waiting for {task_count} active tasks (timeout={timeout}s)")

        # 先唤醒所有等待中的 interrupt，让它们可以正常退出
        for message_id, interrupt in self._interrupts.items():
            if not interrupt.event.is_set():
                interrupt.resume_data = {"approved": False, "reason": "shutdown"}
                interrupt.event.set()

        _, pending = await asyncio.wait(
            self._tasks.values(), timeout=timeout
        )

        if pending:
            logger.warning(f"TaskManager shutdown: cancelling {len(pending)} remaining tasks")
            for task in pending:
                task.cancel()
            # 等待被 cancel 的任务完成（处理 CancelledError）
            await asyncio.gather(*pending, return_exceptions=True)

        self._tasks.clear()
        self._interrupts.clear()
        self._cancellations.clear()
        self._queues.clear()
        self._active_conversations.clear()
        logger.info("TaskManager shutdown complete")

    @property
    def active_task_count(self) -> int:
        """获取活跃任务数量"""
        return len(self._tasks)

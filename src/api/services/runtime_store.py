"""
RuntimeStore — 可替换的运行时状态管理

职责：
- Conversation lease（阻止并发 POST /chat）
- Engine interactive 状态（inject/cancel 有效窗口）
- Interrupt 管理（asyncio.Event 暂停/恢复执行）
- Cancellation 管理
- Message queue（执行中消息注入）

双状态生命周期：
    lease:       try_acquire_lease → release_lease
    interactive: mark_engine_interactive → clear_engine_interactive

    lease 覆盖整个执行周期（含 post-processing），
    interactive 仅覆盖 engine loop（退出后 inject/cancel 返回 409）。
"""

import asyncio
from typing import Protocol, runtime_checkable, Optional, Dict, Any, List, Literal

from core.engine import InterruptState
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@runtime_checkable
class RuntimeStore(Protocol):
    """运行时状态存储协议 — 可替换为 Redis 实现。"""

    # ── Conversation lease（阻止并发 POST /chat）──

    def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]: ...
    def release_lease(self, conversation_id: str) -> None: ...
    def get_leased_message_id(self, conversation_id: str) -> Optional[str]: ...

    # ── Engine interactive（inject/cancel 有效）──

    def mark_engine_interactive(self, conversation_id: str, message_id: str) -> None: ...
    def clear_engine_interactive(self, conversation_id: str) -> None: ...
    def get_interactive_message_id(self, conversation_id: str) -> Optional[str]: ...

    # ── Interrupts ──

    def create_interrupt(self, message_id: str, data: Dict[str, Any]) -> InterruptState: ...
    def resolve_interrupt(self, message_id: str, resume_data: Dict[str, Any]) -> Literal["resolved", "not_found", "already_resolved"]: ...
    def get_interrupt(self, message_id: str) -> Optional[InterruptState]: ...

    # ── Cancellation ──

    def request_cancel(self, message_id: str) -> None: ...
    def is_cancelled(self, message_id: str) -> bool: ...

    # ── Message queue ──

    def inject_message(self, message_id: str, content: str) -> None: ...
    def drain_messages(self, message_id: str) -> List[str]: ...

    # ── Lifecycle ──

    def cleanup_execution(self, message_id: str) -> None: ...
    def shutdown_cleanup(self) -> None: ...


class InMemoryRuntimeStore:
    """
    基于内存的 RuntimeStore 实现

    持有 5 个 dict，每个 dict 对应一个运行时状态维度。
    双状态（lease + interactive）各有独立生命周期。
    """

    def __init__(self):
        self._conversation_leases: dict[str, str] = {}   # conv_id → message_id
        self._engine_interactive: dict[str, str] = {}     # conv_id → message_id
        self._interrupts: dict[str, InterruptState] = {}  # message_id → InterruptState
        self._cancellations: dict[str, asyncio.Event] = {}  # message_id → Event
        self._queues: dict[str, asyncio.Queue] = {}       # message_id → Queue

    # ── Conversation lease ──

    def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]:
        """
        原子地检查并注册 conversation 的 lease。

        Returns:
            None — 获取成功
            str  — 已有 lease 的 message_id（获取失败）
        """
        existing = self._conversation_leases.get(conversation_id)
        if existing:
            return existing
        self._conversation_leases[conversation_id] = message_id
        return None

    def release_lease(self, conversation_id: str) -> None:
        """释放 conversation lease"""
        self._conversation_leases.pop(conversation_id, None)

    def get_leased_message_id(self, conversation_id: str) -> Optional[str]:
        """获取 conversation 当前 lease 的 message_id"""
        return self._conversation_leases.get(conversation_id)

    # ── Engine interactive ──

    def mark_engine_interactive(self, conversation_id: str, message_id: str) -> None:
        """标记 engine 进入可交互状态"""
        self._engine_interactive[conversation_id] = message_id

    def clear_engine_interactive(self, conversation_id: str) -> None:
        """清除 engine 可交互状态（engine 退出后调用）"""
        self._engine_interactive.pop(conversation_id, None)

    def get_interactive_message_id(self, conversation_id: str) -> Optional[str]:
        """获取 conversation 当前可交互的 message_id"""
        return self._engine_interactive.get(conversation_id)

    # ── Interrupts ──

    def create_interrupt(self, message_id: str, data: Dict[str, Any]) -> InterruptState:
        """创建中断状态"""
        interrupt = InterruptState(interrupt_data=data)
        self._interrupts[message_id] = interrupt
        logger.info(f"Interrupt created for {message_id}")
        return interrupt

    def resolve_interrupt(
        self, message_id: str, resume_data: Dict[str, Any]
    ) -> Literal["resolved", "not_found", "already_resolved"]:
        """
        解决中断（用户确认后调用）

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

    # ── Cancellation ──

    def request_cancel(self, message_id: str) -> None:
        """
        请求取消执行。

        设置 cancel flag 并唤醒可能阻塞的 interrupt。
        路由层已通过 get_interactive_message_id 前置检查，此处不再校验。
        """
        if message_id not in self._cancellations:
            self._cancellations[message_id] = asyncio.Event()
        self._cancellations[message_id].set()
        # 同时唤醒可能阻塞的 interrupt，使其不阻碍退出
        interrupt = self._interrupts.get(message_id)
        if interrupt and not interrupt.event.is_set():
            interrupt.resume_data = {"approved": False, "reason": "cancelled"}
            interrupt.event.set()
        logger.info(f"Cancellation requested for {message_id}")

    def is_cancelled(self, message_id: str) -> bool:
        """检查执行是否已被取消"""
        event = self._cancellations.get(message_id)
        return event.is_set() if event else False

    # ── Message queue ──

    def inject_message(self, message_id: str, content: str) -> None:
        """向执行中的任务注入消息"""
        if message_id not in self._queues:
            self._queues[message_id] = asyncio.Queue()
        self._queues[message_id].put_nowait(content)
        logger.debug(f"Message injected for {message_id}")

    def drain_messages(self, message_id: str) -> List[str]:
        """非阻塞地取出所有排队消息"""
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

    # ── Lifecycle ──

    def cleanup_execution(self, message_id: str) -> None:
        """清理指定 message_id 的所有运行时状态"""
        self._interrupts.pop(message_id, None)
        self._cancellations.pop(message_id, None)
        self._queues.pop(message_id, None)
        # 清理 lease 和 interactive 中引用此 message_id 的条目
        self._conversation_leases = {
            k: v for k, v in self._conversation_leases.items() if v != message_id
        }
        self._engine_interactive = {
            k: v for k, v in self._engine_interactive.items() if v != message_id
        }
        logger.debug(f"Execution {message_id} cleaned up from runtime store")

    def shutdown_cleanup(self) -> None:
        """关闭时清理：唤醒所有 pending interrupt + 清空所有 dict"""
        for message_id, interrupt in self._interrupts.items():
            if not interrupt.event.is_set():
                interrupt.resume_data = {"approved": False, "reason": "shutdown"}
                interrupt.event.set()

        self._conversation_leases.clear()
        self._engine_interactive.clear()
        self._interrupts.clear()
        self._cancellations.clear()
        self._queues.clear()
        logger.debug("Runtime store shutdown cleanup complete")

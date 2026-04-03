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

Protocol 方法全部 async，为 Redis 实现铺平接口。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable, Optional, Dict, Any, List, Literal

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@runtime_checkable
class RuntimeStore(Protocol):
    """运行时状态存储协议 — 可替换为 Redis 实现。"""

    # ── Conversation lease（阻止并发 POST /chat）──

    async def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]: ...
    async def release_lease(self, conversation_id: str, message_id: str) -> None: ...
    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]: ...

    # ── Engine interactive（inject/cancel 有效）──

    async def mark_engine_interactive(self, conversation_id: str, message_id: str) -> None: ...
    async def clear_engine_interactive(self, conversation_id: str, message_id: str) -> None: ...
    async def get_interactive_message_id(self, conversation_id: str) -> Optional[str]: ...

    # ── Interrupts ──

    async def wait_for_interrupt(self, message_id: str, data: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]: ...
    async def resolve_interrupt(self, message_id: str, resume_data: Dict[str, Any]) -> Literal["resolved", "not_found", "already_resolved"]: ...
    async def get_interrupt_data(self, message_id: str) -> Optional[Dict[str, Any]]: ...

    # ── Cancellation ──

    async def request_cancel(self, message_id: str) -> None: ...
    async def is_cancelled(self, message_id: str) -> bool: ...

    # ── Message queue ──

    async def inject_message(self, message_id: str, content: str) -> None: ...
    async def drain_messages(self, message_id: str) -> List[str]: ...

    # ── Lifecycle ──

    async def cleanup_execution(self, conversation_id: str, message_id: str) -> None: ...
    async def shutdown_cleanup(self) -> None: ...
    async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool: ...


# ============================================================
# InterruptState — InMemory 内部实现细节（不对外暴露）
# ============================================================

@dataclass
class _InterruptState:
    """中断状态（InMemoryRuntimeStore 内部使用）"""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    interrupt_data: Dict[str, Any] = field(default_factory=dict)
    resume_data: Optional[Dict[str, Any]] = None


class InMemoryRuntimeStore:
    """
    基于内存的 RuntimeStore 实现

    持有 5 个 dict，每个 dict 对应一个运行时状态维度。
    双状态（lease + interactive）各有独立生命周期。
    所有方法 async（dict 操作本身不阻塞，async 为接口一致性）。
    """

    def __init__(self):
        self._conversation_leases: dict[str, str] = {}   # conv_id → message_id
        self._engine_interactive: dict[str, str] = {}     # conv_id → message_id
        self._interrupts: dict[str, _InterruptState] = {}  # message_id → _InterruptState
        self._cancellations: dict[str, asyncio.Event] = {}  # message_id → Event
        self._queues: dict[str, asyncio.Queue] = {}       # message_id → Queue

    # ── Conversation lease ──

    async def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]:
        existing = self._conversation_leases.get(conversation_id)
        if existing:
            return existing
        self._conversation_leases[conversation_id] = message_id
        return None

    async def release_lease(self, conversation_id: str, message_id: str) -> None:
        """释放 conversation lease。InMemory 忽略 msg_id（单进程无竞争）。"""
        self._conversation_leases.pop(conversation_id, None)

    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]:
        return self._conversation_leases.get(conversation_id)

    # ── Engine interactive ──

    async def mark_engine_interactive(self, conversation_id: str, message_id: str) -> None:
        self._engine_interactive[conversation_id] = message_id

    async def clear_engine_interactive(self, conversation_id: str, message_id: str) -> None:
        """清除 engine 可交互状态。InMemory 忽略 msg_id（单进程无竞争）。"""
        self._engine_interactive.pop(conversation_id, None)

    async def get_interactive_message_id(self, conversation_id: str) -> Optional[str]:
        return self._engine_interactive.get(conversation_id)

    # ── Interrupts ──

    async def wait_for_interrupt(self, message_id: str, data: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        """创建中断并阻塞等待恢复数据。超时返回 None。"""
        interrupt = _InterruptState(interrupt_data=data)
        self._interrupts[message_id] = interrupt
        logger.info(f"Interrupt created for {message_id}")

        try:
            await asyncio.wait_for(interrupt.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        return interrupt.resume_data

    async def resolve_interrupt(
        self, message_id: str, resume_data: Dict[str, Any]
    ) -> Literal["resolved", "not_found", "already_resolved"]:
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

    async def get_interrupt_data(self, message_id: str) -> Optional[Dict[str, Any]]:
        """返回中断数据 dict（不暴露内部 _InterruptState）。"""
        interrupt = self._interrupts.get(message_id)
        if not interrupt:
            return None
        return interrupt.interrupt_data

    # ── Cancellation ──

    async def request_cancel(self, message_id: str) -> None:
        if message_id not in self._cancellations:
            self._cancellations[message_id] = asyncio.Event()
        self._cancellations[message_id].set()
        # 同时唤醒可能阻塞的 interrupt，使其不阻碍退出
        interrupt = self._interrupts.get(message_id)
        if interrupt and not interrupt.event.is_set():
            interrupt.resume_data = {"approved": False, "reason": "cancelled"}
            interrupt.event.set()
        logger.info(f"Cancellation requested for {message_id}")

    async def is_cancelled(self, message_id: str) -> bool:
        event = self._cancellations.get(message_id)
        return event.is_set() if event else False

    # ── Message queue ──

    async def inject_message(self, message_id: str, content: str) -> None:
        if message_id not in self._queues:
            self._queues[message_id] = asyncio.Queue()
        self._queues[message_id].put_nowait(content)
        logger.debug(f"Message injected for {message_id}")

    async def drain_messages(self, message_id: str) -> List[str]:
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

    async def cleanup_execution(self, conversation_id: str, message_id: str) -> None:
        """清理指定 message_id 的所有运行时状态"""
        self._interrupts.pop(message_id, None)
        self._cancellations.pop(message_id, None)
        self._queues.pop(message_id, None)
        # 清理 lease 和 interactive — O(1) 条件删除（conversation_id 已知）
        if self._conversation_leases.get(conversation_id) == message_id:
            self._conversation_leases.pop(conversation_id, None)
        if self._engine_interactive.get(conversation_id) == message_id:
            self._engine_interactive.pop(conversation_id, None)
        logger.debug(f"Execution {message_id} cleaned up from runtime store")

    async def shutdown_cleanup(self) -> None:
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

    async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool:
        """心跳续租。InMemory 永远成功（内存无 TTL）。"""
        return True

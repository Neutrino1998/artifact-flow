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

from config import config
from utils.instance import INSTANCE_ID
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class InjectQueueFull(Exception):
    """Raised by inject_message when the pending-message queue is at capacity.

    The engine loop only ever drains this queue; a rejected enqueue never
    reaches the loop. Callers (the inject endpoint) map this to HTTP 429 —
    transient backpressure, since drain_messages empties the queue each round.
    """


@runtime_checkable
class ConversationLeaseStore(Protocol):
    """Conversation lease 的读写与续租边界。"""

    # 跨进程共享?Redis=True(多 worker 共享真相源),InMemory=False(进程本地)。
    # 沙盒 reaper 据此判定能否安全跑(进程本地 store 下它会误删兄弟进程的活沙盒)。
    is_shared: bool

    # ── Conversation lease（阻止并发 POST /chat）──

    async def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]: ...
    async def release_lease(self, conversation_id: str, message_id: str) -> None: ...
    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]: ...

    async def get_lease_owner(self, conversation_id: str) -> Optional[str]:
        """当前 lease 持有实例的 instance_id（观测维度；无 lease → None）。

        Redis 实现读 acquire 时旁挂的 owner key；InMemory 是进程本地
        （lease 存在即本实例持有），退化为返回本进程 INSTANCE_ID。
        """
        ...

    def get_lease_key(self, conversation_id: str) -> str: ...
    async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool: ...


@runtime_checkable
class ConversationLeaseReader(Protocol):
    """只读 lease 视图，供 Router/Admin/Reaper/observability 组合。"""

    is_shared: bool

    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]: ...
    async def get_lease_owner(self, conversation_id: str) -> Optional[str]: ...
    async def list_active_conversations(self) -> List[str]: ...
    async def list_active_executions(self) -> Dict[str, str]: ...


@runtime_checkable
class InteractionStore(Protocol):
    """Engine interactive（RUNNING）状态边界。"""

    async def mark_engine_interactive(self, conversation_id: str, message_id: str) -> bool: ...
    async def clear_engine_interactive(self, conversation_id: str, message_id: str) -> None: ...
    async def get_interactive_message_id(self, conversation_id: str) -> Optional[str]: ...


@runtime_checkable
class InteractionReader(Protocol):
    """只读 RUNNING 状态视图。"""

    async def get_interactive_message_id(self, conversation_id: str) -> Optional[str]: ...


@runtime_checkable
class InterruptStore(Protocol):
    """Permission interrupt 等待/恢复边界。"""

    async def wait_for_interrupt(self, message_id: str, data: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]: ...
    async def resolve_interrupt(
        self, message_id: str, call_id: str, resume_data: Dict[str, Any]
    ) -> Literal["resolved", "not_found", "call_mismatch", "already_resolved"]: ...
    async def get_interrupt_data(self, message_id: str) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class CancellationStore(Protocol):
    """协作式取消标记边界。"""

    async def request_cancel(self, message_id: str) -> None: ...
    async def is_cancelled(self, message_id: str) -> bool: ...


@runtime_checkable
class InjectQueue(Protocol):
    """执行中消息注入队列边界。"""

    async def inject_message(self, message_id: str, content: str) -> None:
        """Enqueue a message for the active execution.

        Raises InjectQueueFull if the pending queue is at capacity
        (config.MAX_INJECT_QUEUE_SIZE) — caller maps this to HTTP 429.
        """
        ...
    async def drain_messages(self, message_id: str) -> List[str]: ...


@runtime_checkable
class RuntimeStateLifecycle(Protocol):
    """消息级易失状态与 store 关停边界，不释放 lease。"""

    async def cleanup_message_state(self, message_id: str) -> None: ...
    async def shutdown_cleanup(self) -> None: ...


@runtime_checkable
class RuntimeStore(
    ConversationLeaseStore,
    ConversationLeaseReader,
    InteractionStore,
    InterruptStore,
    CancellationStore,
    InjectQueue,
    RuntimeStateLifecycle,
    Protocol,
):
    """完整 store 的组合类型；消费者应标注上面的最小协议。"""


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

    is_shared = False  # 进程本地:多副本下状态不互通(契约 = 单进程部署)

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
        """仅释放仍归 ``message_id`` 的 lease，与 Redis owner CAS 对齐。"""
        if self._conversation_leases.get(conversation_id) == message_id:
            self._conversation_leases.pop(conversation_id, None)

    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]:
        return self._conversation_leases.get(conversation_id)

    async def get_lease_owner(self, conversation_id: str) -> Optional[str]:
        """进程本地 store:lease 存在即本实例持有。"""
        if conversation_id in self._conversation_leases:
            return INSTANCE_ID
        return None

    # ── Engine interactive ──

    async def mark_engine_interactive(self, conversation_id: str, message_id: str) -> bool:
        """Mark RUNNING only if this message still owns the conversation lease.

        Returns True if marked, False if the lease was lost/taken over (the
        execution service then aborts). Single process → the check is a plain
        dict comparison; it still matters because a fenced/superseded queued
        task could otherwise clobber a new owner's interactive key on the
        QUEUED→RUNNING edge.
        """
        if self._conversation_leases.get(conversation_id) != message_id:
            return False
        self._engine_interactive[conversation_id] = message_id
        return True

    async def clear_engine_interactive(self, conversation_id: str, message_id: str) -> None:
        """仅清理由 ``message_id`` 写入的 RUNNING 标记。"""
        if self._engine_interactive.get(conversation_id) == message_id:
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
        self, message_id: str, call_id: str, resume_data: Dict[str, Any]
    ) -> Literal["resolved", "not_found", "call_mismatch", "already_resolved"]:
        interrupt = self._interrupts.get(message_id)
        if not interrupt:
            logger.warning(f"No interrupt found for {message_id}")
            return "not_found"

        # No await between lookup, identity check, and event.set(): one asyncio
        # task cannot replace this message's interrupt halfway through resolve.
        # message_id identifies the turn; call_id identifies the exact pending
        # authorization within that turn.
        if interrupt.interrupt_data.get("call_id") != call_id:
            logger.warning(
                f"Stale interrupt resolve rejected for {message_id} (call_id={call_id})"
            )
            return "call_mismatch"

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
            self._queues[message_id] = asyncio.Queue(maxsize=config.MAX_INJECT_QUEUE_SIZE)
        # put_nowait (non-blocking) on purpose: a full queue must fail fast as
        # 429 backpressure, not block the request until the loop drains. The
        # engine loop only drains this queue, so a rejected enqueue is invisible
        # to it — the running turn is unaffected.
        try:
            self._queues[message_id].put_nowait(content)
        except asyncio.QueueFull:
            raise InjectQueueFull(
                f"Inject queue full for {message_id} "
                f"(max {config.MAX_INJECT_QUEUE_SIZE} pending)"
            )
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

    # ── Active conversations ──

    async def list_active_conversations(self) -> List[str]:
        return list(self._conversation_leases.keys())

    async def list_active_executions(self) -> Dict[str, str]:
        return dict(self._conversation_leases)

    # ── Lease key ──

    def get_lease_key(self, conversation_id: str) -> str:
        """InMemory 无跨实例 lease 检查，返回空字符串。"""
        return ""

    # ── Lifecycle ──

    async def cleanup_message_state(self, message_id: str) -> None:
        """清理指定 message_id 的非 Conversation 运行时状态。"""
        self._interrupts.pop(message_id, None)
        self._cancellations.pop(message_id, None)
        self._queues.pop(message_id, None)
        logger.debug(f"Execution {message_id} cleaned up from runtime store")

    async def shutdown_cleanup(self) -> None:
        """关闭时只唤醒 pending interrupt。

        Lease/interactive 必须留到各 TaskScope 完成易失资源清理后
        再按 owner 释放；在这里提前 clear 会重建 shutdown 时的孤儿窗口。
        """
        for message_id, interrupt in self._interrupts.items():
            if not interrupt.event.is_set():
                interrupt.resume_data = {"approved": False, "reason": "shutdown"}
                interrupt.event.set()
        logger.debug("Runtime store shutdown interrupts resolved")

    async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool:
        """InMemory 无 TTL，但仍必须校验 owner 以支持 fencing。"""
        return self._conversation_leases.get(conversation_id) == message_id

"""
ExecutionRunner — 本地 asyncio 任务调度

职责：
- 持有 asyncio.Task 引用，防止 GC 回收
- Semaphore 限制并发数
- 去重（message_id 天然唯一，重复提交抛 DuplicateExecutionError）
- Graceful shutdown

运行时状态（interrupt、cancel、lease 等）委托给 RuntimeStore。
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Callable, Coroutine

from api.services.runtime_store import InMemoryRuntimeStore, RuntimeStore

if TYPE_CHECKING:
    from api.services.stream_transport import StreamTransport
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class DuplicateExecutionError(Exception):
    """重复执行错误（message_id 已存在活跃任务）"""
    pass


class ConflictError(Exception):
    """会话已有活跃执行（lease 冲突）"""
    pass


class ExecutionRunner:
    """
    管理执行的后台任务

    职责：
    - 持有任务引用，防止 GC 回收
    - Semaphore 限制并发数
    - Graceful shutdown
    - 去重（message_id 天然唯一）

    运行时状态委托给 self.store (RuntimeStore)。
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        store: RuntimeStore | None = None,
        lease_ttl: int = 0,
    ):
        self._tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._lease_ttl = lease_ttl  # 0 = 不续租（InMemory 场景）
        self.store: RuntimeStore = store or InMemoryRuntimeStore()

        logger.info(f"ExecutionRunner initialized (max_concurrent={max_concurrent}, lease_ttl={lease_ttl})")

    async def submit(
        self,
        conversation_id: str,
        task_id: str,
        coro_factory: Callable[[], Coroutine],
        *,
        user_id: str,
        stream_transport: "StreamTransport",
    ) -> asyncio.Task:
        """
        提交一个后台任务

        编排生命周期：acquire lease → mark interactive → create stream → run task.
        失败时自动回滚 lease + interactive 状态。

        接收 coroutine factory 而非 coroutine 对象，确保 coroutine 仅在编排成功后
        才被创建，避免预调度失败路径产生未被 await 的孤儿 coroutine。

        Args:
            conversation_id: 对话 ID（用于 cleanup_execution）
            task_id: 任务 ID（message_id）
            coro_factory: 零参数 callable，调用后返回要执行的协程
            user_id: 当前用户 ID（用于 stream owner）
            stream_transport: StreamTransport 实例

        Returns:
            asyncio.Task 实例

        Raises:
            DuplicateExecutionError: task_id 已存在活跃任务
            ConflictError: 会话已有活跃执行（lease 冲突）
        """
        if task_id in self._tasks:
            raise DuplicateExecutionError(f"Execution already running for {task_id}")

        # 原子地获取 conversation lease
        active = await self.store.try_acquire_lease(conversation_id, task_id)
        if active:
            raise ConflictError(
                "An execution is already active for this conversation. "
                "Use POST /chat/{conv_id}/inject to send input to the running execution."
            )

        # lease 之后的所有步骤失败时必须回滚（含 coro_factory 调用）
        try:
            await self.store.mark_engine_interactive(conversation_id, task_id)
            await stream_transport.create_stream(
                task_id,
                owner_user_id=user_id,
                lease_check_key=self.store.get_lease_key(conversation_id),
                lease_expected_owner=task_id,
            )
            coro = coro_factory()
        except Exception:
            try:
                await stream_transport.close_stream(task_id)
            except Exception:
                logger.warning(f"Best-effort close_stream failed for {task_id}")
            await self.store.release_lease(conversation_id, task_id)
            await self.store.clear_engine_interactive(conversation_id, task_id)
            raise

        async def _wrapped():
            heartbeat = None
            try:
                if self._lease_ttl > 0:
                    heartbeat = asyncio.create_task(
                        self._renew_loop(conversation_id, task_id),
                        name=f"heartbeat-{task_id}",
                    )
                async with self._semaphore:
                    await coro
            except asyncio.CancelledError:
                logger.warning(f"Task {task_id} cancelled (lease fencing or shutdown)")
            except Exception:
                logger.exception(f"Task {task_id} failed with unhandled exception")
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
                coro.close()
                self._tasks.pop(task_id, None)
                await self.store.cleanup_execution(conversation_id, task_id)
                await stream_transport.close_stream(task_id)
                logger.debug(f"Task {task_id} completed and cleaned up (active: {len(self._tasks)})")

        task = asyncio.create_task(_wrapped(), name=f"exec-{task_id}")
        self._tasks[task_id] = task
        logger.info(f"Task {task_id} submitted (active: {len(self._tasks)})")
        return task

    async def _renew_loop(self, conversation_id: str, task_id: str) -> None:
        """心跳续租循环（TTL/3 间隔）。

        Lease lost (renew returns False) → cancel the execution task (fencing).
        Transient errors (network blip) → log and retry next interval.
        Consecutive failures ≥ 2 → treat as permanent failure, cancel task (fail-closed).
        """
        interval = self._lease_ttl // 3
        consecutive_failures = 0
        while True:
            await asyncio.sleep(interval)
            try:
                still_owner = await self.store.renew_lease(
                    conversation_id, task_id, ttl=self._lease_ttl
                )
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                logger.warning(
                    f"Heartbeat renewal failed for {task_id} "
                    f"(consecutive_failures={consecutive_failures})"
                )
                if consecutive_failures >= 2:
                    logger.error(
                        f"Heartbeat renewal failed {consecutive_failures} times "
                        f"for {task_id} — fail-closed, cancelling task"
                    )
                    task = self._tasks.get(task_id)
                    if task:
                        task.cancel()
                    return
                continue

            if not still_owner:
                logger.error(f"Lease lost for {task_id} — fencing execution")
                task = self._tasks.get(task_id)
                if task:
                    task.cancel()
                return

    async def shutdown(self, timeout: float = 30.0):
        """
        Graceful shutdown：等待所有运行中任务完成，超时后 cancel 剩余任务

        Args:
            timeout: 等待超时时间（秒）
        """
        if not self._tasks:
            logger.info("ExecutionRunner shutdown: no active tasks")
            return

        task_count = len(self._tasks)
        logger.info(f"ExecutionRunner shutdown: waiting for {task_count} active tasks (timeout={timeout}s)")

        # 1. 唤醒所有 pending interrupt
        await self.store.shutdown_cleanup()

        # 2. 等待任务完成
        _, pending = await asyncio.wait(
            self._tasks.values(), timeout=timeout
        )

        # 3. cancel 超时任务
        if pending:
            logger.warning(f"ExecutionRunner shutdown: cancelling {len(pending)} remaining tasks")
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        # 4. 清理
        self._tasks.clear()
        logger.info("ExecutionRunner shutdown complete")

    @property
    def active_task_count(self) -> int:
        """获取活跃任务数量"""
        return len(self._tasks)

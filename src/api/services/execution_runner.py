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
from typing import Coroutine

from api.services.runtime_store import InMemoryRuntimeStore, RuntimeStore
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class DuplicateExecutionError(Exception):
    """重复执行错误（message_id 已存在活跃任务）"""
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

    async def submit(self, conversation_id: str, task_id: str, coro: Coroutine) -> asyncio.Task:
        """
        提交一个后台任务

        Args:
            conversation_id: 对话 ID（用于 cleanup_execution）
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
            heartbeat = None
            if self._lease_ttl > 0:
                heartbeat = asyncio.create_task(
                    self._renew_loop(conversation_id, task_id),
                    name=f"heartbeat-{task_id}",
                )
            async with self._semaphore:
                try:
                    await coro
                except Exception:
                    logger.exception(f"Task {task_id} failed with unhandled exception")
                finally:
                    if heartbeat is not None:
                        heartbeat.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat
                    self._tasks.pop(task_id, None)
                    await self.store.cleanup_execution(conversation_id, task_id)
                    logger.debug(f"Task {task_id} completed and cleaned up (active: {len(self._tasks)})")

        task = asyncio.create_task(_wrapped(), name=f"exec-{task_id}")
        self._tasks[task_id] = task
        logger.info(f"Task {task_id} submitted (active: {len(self._tasks)})")
        return task

    async def _renew_loop(self, conversation_id: str, task_id: str) -> None:
        """心跳续租循环（TTL/3 间隔）"""
        interval = self._lease_ttl // 3
        while True:
            await asyncio.sleep(interval)
            try:
                await self.store.renew_lease(conversation_id, task_id, ttl=self._lease_ttl)
            except Exception:
                logger.warning(f"Heartbeat renewal failed for {task_id}")

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

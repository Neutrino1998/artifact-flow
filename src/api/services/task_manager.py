"""
TaskManager - Background Task 生命周期管理

解决的问题：
- asyncio.create_task() 返回的 Task 没有被持有引用，可能被 GC 回收
- 没有并发数量限制，N 个用户同时发消息 = N 个并发 LLM 调用
- 服务器 shutdown 时运行中任务被直接 cancel，无 graceful shutdown

设计：
    ┌─────────────────────────────────────────────┐
    │  TaskManager                                │
    │                                             │
    │  _tasks: dict[str, asyncio.Task]  ← 引用    │
    │  _semaphore: asyncio.Semaphore    ← 并发控制 │
    │                                             │
    │  submit(task_id, coro) → Task               │
    │  shutdown(timeout) → graceful stop          │
    └─────────────────────────────────────────────┘
"""

import asyncio
from typing import Coroutine

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class TaskManager:
    """
    管理 graph 执行的后台任务

    职责：
    - 持有任务引用，防止 GC 回收
    - Semaphore 限制并发数
    - Graceful shutdown 支持
    """

    def __init__(self, max_concurrent: int = 10):
        self._tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        logger.info(f"TaskManager initialized (max_concurrent={max_concurrent})")

    async def submit(self, task_id: str, coro: Coroutine) -> asyncio.Task:
        """
        提交一个后台任务

        用 semaphore 包裹以限制并发，持有引用防 GC，完成后自动清理。

        Args:
            task_id: 任务 ID（通常是 thread_id）
            coro: 要执行的协程

        Returns:
            asyncio.Task 实例
        """
        async def _wrapped():
            async with self._semaphore:
                try:
                    await coro
                except Exception:
                    logger.exception(f"Task {task_id} failed with unhandled exception")
                finally:
                    self._tasks.pop(task_id, None)
                    logger.debug(f"Task {task_id} completed and cleaned up (active: {len(self._tasks)})")

        task = asyncio.create_task(_wrapped(), name=f"graph-exec-{task_id}")
        self._tasks[task_id] = task
        logger.info(f"Task {task_id} submitted (active: {len(self._tasks)})")
        return task

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
        logger.info("TaskManager shutdown complete")

    @property
    def active_task_count(self) -> int:
        """获取活跃任务数量"""
        return len(self._tasks)

"""Process-local asyncio task supervision with one capacity gate.

This module deliberately has no Conversation, Redis, SSE, database, or Web
dependencies.  Callers compose workload-specific ownership and transports
through a generic task event sink and ``TaskScope`` cleanup callbacks.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

CLEANUP_CALLBACK_TIMEOUT_SEC = 30


class DuplicateTaskError(Exception):
    """A task with the same process-local ID is already supervised."""


@dataclass(frozen=True)
class TaskQueued:
    """Generic capacity-wait observation emitted before semaphore acquire."""

    ahead: int
    capacity: int


TaskEventSink = Callable[[TaskQueued], Awaitable[None]]
CleanupCallback = Callable[[], Awaitable[None]]
Workload = Callable[[], Awaitable[None]]
WorkloadFactory = Callable[["TaskScope"], Workload]


class TaskScope:
    """Per-task generic event and bounded LIFO-cleanup scope."""

    def __init__(
        self,
        task_id: str,
        *,
        event_sink: Optional[TaskEventSink] = None,
        cleanup_timeout: float = CLEANUP_CALLBACK_TIMEOUT_SEC,
    ) -> None:
        self.task_id = task_id
        self._event_sink = event_sink
        self._cleanup_timeout = cleanup_timeout
        self._cleanups: list[tuple[str, CleanupCallback]] = []
        self._closed = False

    def add_cleanup(self, label: str, callback: CleanupCallback) -> None:
        """Register an idempotent callback; callbacks run in reverse order."""
        if self._closed:
            raise RuntimeError(f"TaskScope {self.task_id} is already closed")
        self._cleanups.append((label, callback))

    async def emit(self, event: TaskQueued) -> None:
        if self._event_sink is not None:
            await self._event_sink(event)

    async def close(self) -> None:
        """Run every cleanup once, bounded and best-effort."""
        if self._closed:
            return
        self._closed = True

        for label, cleanup in reversed(self._cleanups):
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await cleanup()
            except asyncio.CancelledError:
                logger.warning(
                    f"Task {self.task_id} cleanup '{label}' interrupted by cancel; "
                    "continuing teardown"
                )
            except TimeoutError:
                logger.error(
                    f"Task {self.task_id} cleanup '{label}' timed out after "
                    f"{self._cleanup_timeout}s; continuing teardown"
                )
            except Exception:
                logger.exception(
                    f"Task {self.task_id} cleanup '{label}' failed; continuing teardown"
                )
        self._cleanups.clear()


class TaskSupervisor:
    """Own task references, one capacity gate, cancellation, and shutdown."""

    def __init__(
        self,
        max_concurrent: int = 10,
        *,
        cleanup_timeout: float = CLEANUP_CALLBACK_TIMEOUT_SEC,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_started_at: dict[str, float] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._cleanup_timeout = cleanup_timeout
        logger.info(f"TaskSupervisor initialized (max_concurrent={max_concurrent})")

    async def submit(
        self,
        task_id: str,
        workload_factory: WorkloadFactory,
        *,
        event_sink: Optional[TaskEventSink] = None,
    ) -> asyncio.Task[None]:
        """Configure and supervise a workload without creating it before admission.

        ``workload_factory`` is synchronous and returns an async callable, not a
        coroutine object.  This lets it register cleanup before the task exists,
        while avoiding an un-awaited coroutine if the task is fenced while queued.
        """
        if task_id in self._tasks:
            raise DuplicateTaskError(f"Task already running for {task_id}")

        scope = TaskScope(
            task_id,
            event_sink=event_sink,
            cleanup_timeout=self._cleanup_timeout,
        )
        try:
            workload = workload_factory(scope)
        except Exception:
            await scope.close()
            raise

        entered = asyncio.Event()

        async def _wrapped() -> None:
            entered.set()
            try:
                if self._semaphore.locked():
                    ahead = max(
                        0,
                        len(self._tasks) - self._max_concurrent - 1,
                    )
                    await scope.emit(
                        TaskQueued(ahead=ahead, capacity=self._max_concurrent)
                    )
                async with self._semaphore:
                    await workload()
            except asyncio.CancelledError:
                logger.warning(f"Task {task_id} cancelled")
            except Exception:
                logger.exception(f"Task {task_id} failed with unhandled exception")
            finally:
                await scope.close()
                self._tasks.pop(task_id, None)
                self._task_started_at.pop(task_id, None)
                logger.info(
                    f"Task {task_id} completed and cleaned up "
                    f"(active: {len(self._tasks)})"
                )

        task = asyncio.create_task(_wrapped(), name=f"task-{task_id}")
        self._tasks[task_id] = task
        self._task_started_at[task_id] = time.monotonic()
        await entered.wait()
        logger.info(f"Task {task_id} submitted (active: {len(self._tasks)})")
        return task

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self, timeout: float = 30.0) -> None:
        if not self._tasks:
            logger.info("TaskSupervisor shutdown: no active tasks")
            return

        task_count = len(self._tasks)
        logger.info(
            f"TaskSupervisor shutdown: waiting for {task_count} active tasks "
            f"(timeout={timeout}s)"
        )
        _, pending = await asyncio.wait(list(self._tasks.values()), timeout=timeout)
        if pending:
            logger.warning(
                f"TaskSupervisor shutdown: cancelling {len(pending)} remaining tasks"
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._task_started_at.clear()
        logger.info("TaskSupervisor shutdown complete")

    @property
    def active_task_count(self) -> int:
        return len(self._tasks)

    def long_running_count(self, threshold_sec: float) -> int:
        if not self._task_started_at:
            return 0
        now = time.monotonic()
        return sum(
            1
            for started in self._task_started_at.values()
            if now - started > threshold_sec
        )

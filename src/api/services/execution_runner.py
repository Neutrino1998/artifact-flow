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
import time
from typing import TYPE_CHECKING, Callable, Coroutine

from api.services.runtime_store import InMemoryRuntimeStore, RuntimeStore
from core.events import StreamEventType
from utils.time import utc_now

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
        # task_id → monotonic 起始时刻;observability 的 long_running_count 用。
        # 与 _tasks 同生同灭(submit set / finally pop),不暴露给业务路径。
        self._task_started_at: dict[str, float] = {}
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

        编排生命周期：acquire lease → create stream（QUEUED）→ [semaphore] →
        mark interactive（RUNNING 起点）→ run task。submit 阶段只持 lease + 建
        stream（承载 execution_queued 事件），失败时回滚两者。mark_interactive
        不在此处 —— 它标记 QUEUED→RUNNING 边，移到 _wrapped 取得 semaphore 之后。

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

        # lease 之后的所有步骤失败时必须回滚（含 coro_factory 调用）。
        # 此处只做 QUEUED 阶段的副作用：create stream（必须在 submit 建好，因为
        # execution_queued 事件在取 semaphore 之前就推到它上面）。interactive 留到
        # _wrapped 取得 semaphore 之后再标记，所以回滚里也不需要 clear interactive。
        try:
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
            raise

        async def _wrapped():
            heartbeat = None
            try:
                if self._lease_ttl > 0:
                    heartbeat = asyncio.create_task(
                        self._renew_loop(conversation_id, task_id),
                        name=f"heartbeat-{task_id}",
                    )
                # 信号量已满 → 先推一条 execution_queued 让前端显示排队 UI;
                # 否则到 agent_start 之间是静默挂起。SSE-only,不持久化。
                # ahead 是上界估计:_tasks 包含本任务+排队中+运行中,asyncio.Semaphore
                # 的 _waiters 是私有且 FIFO,无法精确算我的真实位次。
                #
                # 已知 FIFO 抖动:Redis transport 下 push_event 是真异步(HGET+XADD
                # ~ms 级),`await` 会让出事件循环,理论上让稍晚入队的 task 抢先到
                # `async with self._semaphore` 的 acquire() 排号点(InMemory 下
                # push_event 全同步,无此问题)。窗口仅 ~ms 级,影响是 ±1 位次的
                # FIFO 抖动,不是 starvation;ahead 本身已是上界估计,接受现状。
                # 若未来换更慢的 transport 触发明显不公平,可改 fire-and-forget
                # (asyncio.create_task) + 前端按 segments.length 守卫晚到的
                # execution_queued 事件。
                if self._semaphore.locked():
                    ahead = max(0, len(self._tasks) - self._max_concurrent - 1)
                    await stream_transport.push_event(task_id, {
                        "type": StreamEventType.EXECUTION_QUEUED.value,
                        "timestamp": utc_now().isoformat(),
                        "data": {
                            "ahead": ahead,
                            "max_concurrent": self._max_concurrent,
                        },
                    })
                async with self._semaphore:
                    # QUEUED → RUNNING 边：取得并发槽位后，对 lease owner 做 compare-and-set。
                    # 只有仍持有 conversation lease 时才标记 interactive 并启动引擎。
                    # interactive 窗口因此恰好等于 RUNNING（与 _on_engine_exit 处的 clear
                    # 对称）；inject / cancel 都 gate 在它。
                    #
                    # 若排队期间 lease 已过期 / 被新一轮接管（而 heartbeat 还没来得及 fence
                    # 本 task），则**不能**启动 —— 否则会 (a) 覆盖新 owner 的 interactive key、
                    # (b) 在该会话上跑成第二写者（破坏 lease 单写不变量）。此时 abort，finally
                    # 走 cleanup（compare-and-del 不会动到新 owner 的 key），本轮成为 ORPHANED。
                    #
                    # fail-closed：CAS 抛异常时归属不可知 —— 宁可 abort（本轮成 ORPHANED，
                    # 响亮可见、可刷新恢复）也不能 fail-open 跑成静默的第二写者（破坏单写不变量，
                    # 远比丢一个 turn 严重；codebase 偏好 loud failure over silent corruption）。
                    # redis-py client 已对瞬断重试；仍抛即视为持续故障 → abort。
                    try:
                        owns_lease = await self.store.mark_engine_interactive(conversation_id, task_id)
                    except Exception:
                        logger.warning(
                            f"mark_engine_interactive errored for {task_id}; cannot confirm "
                            f"lease ownership — aborting before run (fail-closed)"
                        )
                        owns_lease = False
                    if not owns_lease:
                        logger.warning(
                            f"Task {task_id} no longer owns its conversation lease "
                            f"(lost while queued / unconfirmed); aborting before run to avoid a second writer"
                        )
                        return
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
                self._task_started_at.pop(task_id, None)
                await self.store.cleanup_execution(conversation_id, task_id)
                await stream_transport.close_stream(task_id)
                # 提到 INFO:与 L152 submit 日志对称(都是任务生命周期边界);
                # 一个 turn 一条,频率合理,事故诊断必需。
                logger.info(f"Task {task_id} completed and cleaned up (active: {len(self._tasks)})")

        task = asyncio.create_task(_wrapped(), name=f"exec-{task_id}")
        self._tasks[task_id] = task
        # 起始时刻必须在 create_task 之后、return 之前 set,保证 sampler 读到时
        # 一定能匹配上 _tasks 里的条目(无窗口)。
        self._task_started_at[task_id] = time.monotonic()
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

    def long_running_count(self, threshold_sec: float) -> int:
        """返回运行时长超过 threshold_sec 的活跃任务数。

        给 observability sampler 用 — /admin/runtime 的半活诊断信号。读
        _task_started_at 的快照,不持锁(dict get 在 CPython 是 GIL-atomic;
        sampler 跑在 asyncio 线程,与 submit/finally 同步)。任务在我们计算
        途中完成也无碍,小数字偏差不影响诊断用途。
        """
        if not self._task_started_at:
            return 0
        now = time.monotonic()
        return sum(
            1 for started in self._task_started_at.values()
            if now - started > threshold_sec
        )

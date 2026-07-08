"""
run_cancellable unit tests (core/cancellation.py).

Covers:
- result / exception passthrough (the wrapper must be transparent when no
  cancel happens)
- cooperative cancel: flag trips mid-await → CooperativeCancelled raised AND
  the in-flight child task actually receives task.cancel() (no orphan)
- external cancel of the CALLER task: forwarded into the child, then re-raised
  as CancelledError (never converted — the two paths must not blur)
- is_cancelled predicate raising (e.g. Redis down): child cancelled, original
  exception propagates
"""

import asyncio

import pytest

from core.cancellation import CooperativeCancelled, run_cancellable


async def _never_cancelled() -> bool:
    return False


async def _always_cancelled() -> bool:
    return True


def _hanging_work(child_cancelled: asyncio.Event, started: asyncio.Event = None):
    """A work coroutine that hangs until cancelled, recording the cancel."""
    async def work():
        if started is not None:
            started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            child_cancelled.set()
            raise
        return "never"
    return work()


class TestPassthrough:

    async def test_result_passthrough(self):
        async def work():
            return 42
        assert await run_cancellable(work(), _never_cancelled, 0.01) == 42

    async def test_exception_passthrough(self):
        async def work():
            raise ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            await run_cancellable(work(), _never_cancelled, 0.01)

    async def test_fast_result_wins_even_with_flag_set(self):
        """Work completing inside the first wait window returns its result —
        the flag is only consulted when the poll fires."""
        async def work():
            return "fast"
        assert await run_cancellable(work(), _always_cancelled, 5.0) == "fast"


class TestCooperativeCancel:

    async def test_raises_and_cancels_child(self):
        child_cancelled = asyncio.Event()
        with pytest.raises(CooperativeCancelled):
            await run_cancellable(_hanging_work(child_cancelled), _always_cancelled, 0.01)
        assert child_cancelled.is_set()

    async def test_flag_trips_mid_flight(self):
        """Flag false at first polls, set later → cancel lands on the poll after."""
        flag = {"v": False}

        async def check():
            return flag["v"]

        child_cancelled = asyncio.Event()
        started = asyncio.Event()

        async def trip_later():
            await started.wait()
            flag["v"] = True

        tripper = asyncio.create_task(trip_later())
        with pytest.raises(CooperativeCancelled):
            await run_cancellable(_hanging_work(child_cancelled, started), check, 0.01)
        assert child_cancelled.is_set()
        await tripper


class TestExternalCancel:

    async def test_forwards_to_child_and_reraises(self):
        """Cancelling the caller task (lease fencing / EXECUTION_TIMEOUT shape)
        must cancel the child too — asyncio.wait does NOT do that on its own —
        and re-raise CancelledError unconverted."""
        child_cancelled = asyncio.Event()
        started = asyncio.Event()

        outer = asyncio.create_task(
            run_cancellable(_hanging_work(child_cancelled, started), _never_cancelled, 0.01)
        )
        await started.wait()
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        assert child_cancelled.is_set()

    async def test_external_cancel_during_cleanup_wins_over_cooperative(self):
        """外部 cancel 落在「协作取消已触发、正在 await 子 task 收尾」的窗口 →
        透出的必须是 CancelledError（外部语义优先），不能被收尾段吞掉后改写成
        CooperativeCancelled —— 否则 fenced task 会以协作路径继续跑 post-processing
        （静默第二写者）/ TIMED_OUT 被记成 CANCELLED。reviewer F1 回归。"""
        cleanup_entered = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def work():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cleanup_entered.set()
                await release_cleanup.wait()  # 把收尾窗口撑开
                raise

        outer = asyncio.create_task(run_cancellable(work(), _always_cancelled, 0.01))
        # 协作取消已触发（子 task 进入收尾）、helper 正阻塞在 await task 上
        await cleanup_entered.wait()
        outer.cancel()  # 外部 cancel 打进收尾窗口
        with pytest.raises(asyncio.CancelledError):
            await outer
        # 放掉子 task 收尾，避免测试退出时留 pending task
        release_cleanup.set()
        await asyncio.sleep(0.01)


class TestPredicateFailure:

    async def test_predicate_error_cancels_child_and_propagates(self):
        async def bad_flag():
            raise RuntimeError("redis down")

        child_cancelled = asyncio.Event()
        with pytest.raises(RuntimeError, match="redis down"):
            await run_cancellable(_hanging_work(child_cancelled), bad_flag, 0.01)
        assert child_cancelled.is_set()

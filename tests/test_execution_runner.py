"""
ExecutionRunner unit tests.

Pure asyncio — no DB, no LLM mocks needed.
"""

import asyncio

import pytest

from api.services.execution_runner import DuplicateExecutionError, ExecutionRunner
from api.services.runtime_store import InMemoryRuntimeStore


# ============================================================
# Helpers
# ============================================================


async def _noop_coro():
    """Coroutine that completes immediately."""
    pass


async def _blocking_coro(event: asyncio.Event):
    """Coroutine that blocks until *event* is set."""
    await event.wait()


async def _failing_coro():
    """Coroutine that raises an exception."""
    raise RuntimeError("boom")


# ============================================================
# TestSubmit
# ============================================================


class TestSubmit:

    async def test_submit_runs_and_cleans_up(self):
        runner = ExecutionRunner(max_concurrent=5)
        done = asyncio.Event()

        async def coro():
            done.set()

        task = await runner.submit("t1", coro())
        await done.wait()
        await task  # let cleanup run
        await asyncio.sleep(0)  # yield to allow finally block
        assert runner.active_task_count == 0

    async def test_duplicate_task_id_raises(self):
        runner = ExecutionRunner()
        blocker = asyncio.Event()
        await runner.submit("t1", _blocking_coro(blocker))

        dup_coro = _noop_coro()
        with pytest.raises(DuplicateExecutionError):
            await runner.submit("t1", dup_coro)
        dup_coro.close()  # prevent "coroutine was never awaited" warning

        blocker.set()
        await runner.shutdown(timeout=2)

    async def test_semaphore_limits_concurrency(self):
        runner = ExecutionRunner(max_concurrent=2)
        release1 = asyncio.Event()
        release2 = asyncio.Event()
        t1_started = asyncio.Event()
        t2_started = asyncio.Event()

        async def coro1():
            t1_started.set()
            await release1.wait()

        async def coro2():
            t2_started.set()
            await release2.wait()

        await runner.submit("t1", coro1())
        await runner.submit("t2", coro2())
        await t1_started.wait()
        await t2_started.wait()

        # Third task can't acquire semaphore yet (2 tasks hold it)
        t3_started = asyncio.Event()

        async def coro3():
            t3_started.set()

        await runner.submit("t3", coro3())
        await asyncio.sleep(0.05)
        assert not t3_started.is_set()

        # Release one slot → t3 should start
        release1.set()
        await asyncio.sleep(0.05)
        assert t3_started.is_set()

        release2.set()
        await runner.shutdown(timeout=2)

    async def test_exception_cleans_up(self):
        runner = ExecutionRunner()
        task = await runner.submit("t1", _failing_coro())
        await asyncio.sleep(0.05)
        assert runner.active_task_count == 0

    async def test_completion_delegates_cleanup_to_store(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        blocker = asyncio.Event()

        await runner.submit("t1", _blocking_coro(blocker))

        # Set up store state that should be cleaned up
        store.try_acquire_lease("conv-1", "t1")
        store.mark_engine_interactive("conv-1", "t1")
        store.create_interrupt("t1", {"tool": "x"})
        store._cancellations["t1"] = asyncio.Event()
        store._queues["t1"] = asyncio.Queue()

        blocker.set()
        await runner.shutdown(timeout=2)

        assert "t1" not in runner._tasks
        assert store.get_interrupt("t1") is None
        assert store.get_leased_message_id("conv-1") is None
        assert store.get_interactive_message_id("conv-1") is None


# ============================================================
# TestShutdown
# ============================================================


class TestShutdown:

    async def test_shutdown_no_tasks(self):
        runner = ExecutionRunner()
        await runner.shutdown(timeout=1)
        # Should complete immediately without error

    async def test_shutdown_waits_for_active(self):
        runner = ExecutionRunner()
        completed = asyncio.Event()

        async def slow_coro():
            await asyncio.sleep(0.1)
            completed.set()

        await runner.submit("t1", slow_coro())
        await runner.shutdown(timeout=5)
        assert completed.is_set()

    async def test_shutdown_cancels_on_timeout(self):
        runner = ExecutionRunner()
        blocker = asyncio.Event()
        await runner.submit("t1", _blocking_coro(blocker))

        await runner.shutdown(timeout=0.1)
        # Task should be cancelled after timeout
        assert runner.active_task_count == 0

    async def test_shutdown_calls_store_shutdown_cleanup(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        blocker = asyncio.Event()

        await runner.submit("msg-1", _blocking_coro(blocker))
        interrupt = store.create_interrupt("msg-1", {})

        # Start shutdown in background
        shutdown_task = asyncio.create_task(runner.shutdown(timeout=2))
        await asyncio.sleep(0.05)

        # Interrupt should have been woken with shutdown reason
        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": False, "reason": "shutdown"}

        blocker.set()
        await shutdown_task

    async def test_shutdown_clears_tasks(self):
        runner = ExecutionRunner()
        blocker = asyncio.Event()
        await runner.submit("t1", _blocking_coro(blocker))

        blocker.set()
        await runner.shutdown(timeout=2)

        assert len(runner._tasks) == 0

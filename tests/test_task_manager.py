"""
TaskManager unit tests.

Pure asyncio — no DB, no LLM mocks needed.
"""

import asyncio

import pytest

from api.services.task_manager import (
    DuplicateExecutionError,
    InterruptState,
    TaskManager,
)


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
        tm = TaskManager(max_concurrent=5)
        done = asyncio.Event()

        async def coro():
            done.set()

        task = await tm.submit("t1", coro())
        await done.wait()
        await task  # let cleanup run
        await asyncio.sleep(0)  # yield to allow finally block
        assert tm.active_task_count == 0

    async def test_duplicate_task_id_raises(self):
        tm = TaskManager()
        blocker = asyncio.Event()
        await tm.submit("t1", _blocking_coro(blocker))

        dup_coro = _noop_coro()
        with pytest.raises(DuplicateExecutionError):
            await tm.submit("t1", dup_coro)
        dup_coro.close()  # prevent "coroutine was never awaited" warning

        blocker.set()
        await tm.shutdown(timeout=2)

    async def test_semaphore_limits_concurrency(self):
        tm = TaskManager(max_concurrent=2)
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

        await tm.submit("t1", coro1())
        await tm.submit("t2", coro2())
        await t1_started.wait()
        await t2_started.wait()

        # Third task can't acquire semaphore yet (2 tasks hold it)
        t3_started = asyncio.Event()

        async def coro3():
            t3_started.set()

        await tm.submit("t3", coro3())
        await asyncio.sleep(0.05)
        assert not t3_started.is_set()

        # Release one slot → t3 should start
        release1.set()
        await asyncio.sleep(0.05)
        assert t3_started.is_set()

        release2.set()
        await tm.shutdown(timeout=2)

    async def test_exception_cleans_up(self):
        tm = TaskManager()
        task = await tm.submit("t1", _failing_coro())
        await asyncio.sleep(0.05)
        assert tm.active_task_count == 0

    async def test_completion_clears_all_dicts(self):
        tm = TaskManager()
        blocker = asyncio.Event()

        await tm.submit("t1", _blocking_coro(blocker))
        tm.create_interrupt("t1", {"tool": "x"})
        tm._cancellations["t1"] = asyncio.Event()
        tm._queues["t1"] = asyncio.Queue()
        tm._active_conversations["conv-1"] = "t1"

        blocker.set()
        await tm.shutdown(timeout=2)

        assert "t1" not in tm._tasks
        assert "t1" not in tm._interrupts
        assert "t1" not in tm._cancellations
        assert "t1" not in tm._queues
        assert "conv-1" not in tm._active_conversations


# ============================================================
# TestInterrupt
# ============================================================


class TestInterrupt:

    async def test_create_and_resolve(self):
        tm = TaskManager()
        interrupt = tm.create_interrupt("msg-1", {"tool": "web_search"})
        assert isinstance(interrupt, InterruptState)
        assert not interrupt.event.is_set()

        result = await tm.resolve_interrupt("msg-1", {"approved": True})
        assert result == "resolved"
        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": True}

    async def test_resolve_nonexistent_returns_not_found(self):
        tm = TaskManager()
        result = await tm.resolve_interrupt("msg-x", {"approved": True})
        assert result == "not_found"

    async def test_resolve_already_resolved(self):
        tm = TaskManager()
        tm.create_interrupt("msg-1", {})
        await tm.resolve_interrupt("msg-1", {"approved": True})
        result = await tm.resolve_interrupt("msg-1", {"approved": False})
        assert result == "already_resolved"

    async def test_get_interrupt(self):
        tm = TaskManager()
        tm.create_interrupt("msg-1", {"tool": "test"})

        interrupt = tm.get_interrupt("msg-1")
        assert interrupt is not None
        assert interrupt.interrupt_data == {"tool": "test"}

        assert tm.get_interrupt("nonexistent") is None


# ============================================================
# TestCancellation
# ============================================================


class TestCancellation:

    async def test_cancel_active_task(self):
        tm = TaskManager()
        blocker = asyncio.Event()
        await tm.submit("msg-1", _blocking_coro(blocker))

        assert tm.request_cancel("msg-1") is True
        assert tm.is_cancelled("msg-1") is True

        blocker.set()
        await tm.shutdown(timeout=2)

    async def test_cancel_no_task_returns_false(self):
        tm = TaskManager()
        assert tm.request_cancel("msg-x") is False

    async def test_cancel_wakes_pending_interrupt(self):
        tm = TaskManager()
        blocker = asyncio.Event()
        await tm.submit("msg-1", _blocking_coro(blocker))

        interrupt = tm.create_interrupt("msg-1", {"tool": "x"})
        assert not interrupt.event.is_set()

        tm.request_cancel("msg-1")
        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": False, "reason": "cancelled"}

        blocker.set()
        await tm.shutdown(timeout=2)

    async def test_is_cancelled_no_event(self):
        tm = TaskManager()
        assert tm.is_cancelled("msg-x") is False


# ============================================================
# TestConversationReservation
# ============================================================


class TestConversationReservation:

    async def test_reserve_success(self):
        tm = TaskManager()
        result = tm.try_reserve_conversation("conv-1", "msg-1")
        assert result is None  # success

    async def test_duplicate_reserve(self):
        tm = TaskManager()
        tm.try_reserve_conversation("conv-1", "msg-1")
        result = tm.try_reserve_conversation("conv-1", "msg-2")
        assert result == "msg-1"  # already reserved

    async def test_unregister_allows_re_reserve(self):
        tm = TaskManager()
        tm.try_reserve_conversation("conv-1", "msg-1")
        tm.unregister_conversation("conv-1")
        result = tm.try_reserve_conversation("conv-1", "msg-2")
        assert result is None

    async def test_get_active_message_id_with_task(self):
        tm = TaskManager()
        blocker = asyncio.Event()
        await tm.submit("msg-1", _blocking_coro(blocker))
        tm.try_reserve_conversation("conv-1", "msg-1")

        assert tm.get_active_message_id("conv-1") == "msg-1"

        blocker.set()
        await tm.shutdown(timeout=2)

    async def test_get_active_message_id_stale_cleans_up(self):
        tm = TaskManager()
        # Reserve without an active task → stale
        tm._active_conversations["conv-1"] = "msg-stale"

        result = tm.get_active_message_id("conv-1")
        assert result is None
        assert "conv-1" not in tm._active_conversations


# ============================================================
# TestMessageQueue
# ============================================================


class TestMessageQueue:

    async def test_inject_and_drain(self):
        tm = TaskManager()
        tm.inject_message("msg-1", "hello")
        tm.inject_message("msg-1", "world")
        tm.inject_message("msg-1", "!")

        messages = tm.drain_messages("msg-1")
        assert messages == ["hello", "world", "!"]

    async def test_drain_empty(self):
        tm = TaskManager()
        assert tm.drain_messages("msg-x") == []

    async def test_inject_auto_creates_queue(self):
        tm = TaskManager()
        assert "msg-1" not in tm._queues
        tm.inject_message("msg-1", "auto")
        assert "msg-1" in tm._queues


# ============================================================
# TestShutdown
# ============================================================


class TestShutdown:

    async def test_shutdown_no_tasks(self):
        tm = TaskManager()
        await tm.shutdown(timeout=1)
        # Should complete immediately without error

    async def test_shutdown_waits_for_active(self):
        tm = TaskManager()
        completed = asyncio.Event()

        async def slow_coro():
            await asyncio.sleep(0.1)
            completed.set()

        await tm.submit("t1", slow_coro())
        await tm.shutdown(timeout=5)
        assert completed.is_set()

    async def test_shutdown_cancels_on_timeout(self):
        tm = TaskManager()
        blocker = asyncio.Event()
        await tm.submit("t1", _blocking_coro(blocker))

        await tm.shutdown(timeout=0.1)
        # Task should be cancelled after timeout
        assert tm.active_task_count == 0

    async def test_shutdown_wakes_interrupts(self):
        tm = TaskManager()
        blocker = asyncio.Event()
        await tm.submit("msg-1", _blocking_coro(blocker))
        interrupt = tm.create_interrupt("msg-1", {})

        # Start shutdown in background
        shutdown_task = asyncio.create_task(tm.shutdown(timeout=2))
        await asyncio.sleep(0.05)

        # Interrupt should have been woken with shutdown reason
        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": False, "reason": "shutdown"}

        blocker.set()
        await shutdown_task

    async def test_shutdown_clears_all_state(self):
        tm = TaskManager()
        blocker = asyncio.Event()
        await tm.submit("t1", _blocking_coro(blocker))
        tm.create_interrupt("t1", {})
        tm._cancellations["t1"] = asyncio.Event()
        tm._queues["t1"] = asyncio.Queue()
        tm._active_conversations["c1"] = "t1"

        blocker.set()
        await tm.shutdown(timeout=2)

        assert len(tm._tasks) == 0
        assert len(tm._interrupts) == 0
        assert len(tm._cancellations) == 0
        assert len(tm._queues) == 0
        assert len(tm._active_conversations) == 0

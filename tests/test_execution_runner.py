"""
ExecutionRunner unit tests.

Pure asyncio — no DB, no LLM mocks needed.
"""

import asyncio

import pytest

from api.services.execution_runner import ConflictError, DuplicateExecutionError, ExecutionRunner
from api.services.runtime_store import InMemoryRuntimeStore, _InterruptState


# ============================================================
# Helpers
# ============================================================


class _MockStreamTransport:
    """Minimal mock for StreamTransport — satisfies submit() orchestration."""
    async def create_stream(self, stream_id, owner_user_id=None): pass
    async def close_stream(self, stream_id): return True


_mock_transport = _MockStreamTransport()


async def _noop_coro():
    """Coroutine that completes immediately."""
    pass


def _blocking_factory(event: asyncio.Event):
    """Return a coro factory that blocks until *event* is set."""
    async def _coro():
        await event.wait()
    return _coro


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

        task = await runner.submit("conv-1", "t1", coro, user_id="u1", stream_transport=_mock_transport)
        await done.wait()
        await task  # let cleanup run
        await asyncio.sleep(0)  # yield to allow finally block
        assert runner.active_task_count == 0

    async def test_duplicate_task_id_raises(self):
        runner = ExecutionRunner()
        blocker = asyncio.Event()
        await runner.submit("conv-1", "t1", _blocking_factory(blocker), user_id="u1", stream_transport=_mock_transport)

        with pytest.raises(DuplicateExecutionError):
            await runner.submit("conv-1", "t1", _noop_coro, user_id="u1", stream_transport=_mock_transport)

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

        await runner.submit("conv-1", "t1", coro1, user_id="u1", stream_transport=_mock_transport)
        await runner.submit("conv-2", "t2", coro2, user_id="u1", stream_transport=_mock_transport)
        await t1_started.wait()
        await t2_started.wait()

        # Third task can't acquire semaphore yet (2 tasks hold it)
        t3_started = asyncio.Event()

        async def coro3():
            t3_started.set()

        await runner.submit("conv-3", "t3", coro3, user_id="u1", stream_transport=_mock_transport)
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
        task = await runner.submit("conv-1", "t1", _failing_coro, user_id="u1", stream_transport=_mock_transport)
        await asyncio.sleep(0.05)
        assert runner.active_task_count == 0

    async def test_completion_delegates_cleanup_to_store(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        blocker = asyncio.Event()

        # submit() now acquires lease + marks interactive internally
        await runner.submit("conv-1", "t1", _blocking_factory(blocker), user_id="u1", stream_transport=_mock_transport)

        # Set up additional store state that should be cleaned up
        store._interrupts["t1"] = _InterruptState(interrupt_data={"tool": "x"})
        store._cancellations["t1"] = asyncio.Event()
        store._queues["t1"] = asyncio.Queue()

        blocker.set()
        await runner.shutdown(timeout=2)

        assert "t1" not in runner._tasks
        assert await store.get_interrupt_data("t1") is None
        assert await store.get_leased_message_id("conv-1") is None
        assert await store.get_interactive_message_id("conv-1") is None


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

        await runner.submit("conv-1", "t1", slow_coro, user_id="u1", stream_transport=_mock_transport)
        await runner.shutdown(timeout=5)
        assert completed.is_set()

    async def test_shutdown_cancels_on_timeout(self):
        runner = ExecutionRunner()
        blocker = asyncio.Event()
        await runner.submit("conv-1", "t1", _blocking_factory(blocker), user_id="u1", stream_transport=_mock_transport)

        await runner.shutdown(timeout=0.1)
        # Task should be cancelled after timeout
        assert runner.active_task_count == 0

    async def test_shutdown_calls_store_shutdown_cleanup(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        blocker = asyncio.Event()

        await runner.submit("conv-1", "msg-1", _blocking_factory(blocker), user_id="u1", stream_transport=_mock_transport)
        store._interrupts["msg-1"] = _InterruptState(interrupt_data={})

        # Capture interrupt for verification
        interrupt = store._interrupts["msg-1"]

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
        await runner.submit("conv-1", "t1", _blocking_factory(blocker), user_id="u1", stream_transport=_mock_transport)

        blocker.set()
        await runner.shutdown(timeout=2)

        assert len(runner._tasks) == 0


# ============================================================
# TestSubmitOrchestration — lease / interactive / stream lifecycle
# ============================================================


class TestSubmitOrchestration:

    async def test_submit_acquires_lease_and_marks_interactive(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        blocker = asyncio.Event()

        await runner.submit("conv-1", "t1", _blocking_factory(blocker), user_id="u1", stream_transport=_mock_transport)

        # Verify lease + interactive were set
        assert await store.get_leased_message_id("conv-1") == "t1"
        assert await store.get_interactive_message_id("conv-1") == "t1"

        blocker.set()
        await runner.shutdown(timeout=2)

    async def test_submit_conflict_error(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        blocker = asyncio.Event()

        await runner.submit("conv-1", "t1", _blocking_factory(blocker), user_id="u1", stream_transport=_mock_transport)

        # Second submit for same conversation → ConflictError (no orphan coroutine)
        with pytest.raises(ConflictError):
            await runner.submit("conv-1", "t2", _noop_coro, user_id="u1", stream_transport=_mock_transport)

        blocker.set()
        await runner.shutdown(timeout=2)

    async def test_submit_rollback_on_stream_create_failure(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)

        class _FailingTransport:
            async def create_stream(self, stream_id, owner_user_id=None):
                raise RuntimeError("stream create failed")
            async def close_stream(self, stream_id): return True

        # No orphan coroutine — factory is never called on failure
        with pytest.raises(RuntimeError, match="stream create failed"):
            await runner.submit("conv-1", "t1", _noop_coro, user_id="u1", stream_transport=_FailingTransport())

        # Lease + interactive should have been rolled back
        assert await store.get_leased_message_id("conv-1") is None
        assert await store.get_interactive_message_id("conv-1") is None

    async def test_submit_rollback_on_factory_failure(self):
        from api.services.stream_transport import InMemoryStreamTransport

        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        transport = InMemoryStreamTransport(ttl_seconds=60)

        def bad_factory():
            raise RuntimeError("factory exploded")

        with pytest.raises(RuntimeError, match="factory exploded"):
            await runner.submit("conv-1", "t1", bad_factory, user_id="u1", stream_transport=transport)

        # Lease + interactive + stream should all have been rolled back
        assert await store.get_leased_message_id("conv-1") is None
        assert await store.get_interactive_message_id("conv-1") is None
        assert transport.get_stream_status("t1") != "pending"


# ============================================================
# TestLeaseFencing — renew_lease fencing behavior
# ============================================================


class _FencingStore(InMemoryRuntimeStore):
    """InMemoryRuntimeStore with controllable renew_lease behavior."""

    def __init__(self):
        super().__init__()
        self._renew_result: bool = True
        self._renew_error: Exception | None = None

    async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool:
        if self._renew_error is not None:
            raise self._renew_error
        return self._renew_result


class TestLeaseFencing:

    async def test_renew_false_cancels_task(self):
        """renew_lease returns False → task is cancelled via fencing."""
        store = _FencingStore()
        runner = ExecutionRunner(max_concurrent=5, store=store, lease_ttl=3)
        blocker = asyncio.Event()
        cancelled = asyncio.Event()

        async def coro():
            try:
                await blocker.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = await runner.submit("conv-1", "t1", coro, user_id="u1", stream_transport=_mock_transport)

        # Let heartbeat run once, then make lease lost
        await asyncio.sleep(0.05)
        store._renew_result = False

        # Wait for fencing to kick in (heartbeat interval = lease_ttl // 3 = 1s, but
        # our lease_ttl=3 so interval=1s — we need to wait for the sleep to pass)
        await asyncio.sleep(1.5)

        assert cancelled.is_set(), "Task should have been cancelled by lease fencing"
        # Task should have completed cleanup
        await asyncio.sleep(0.1)
        assert runner.active_task_count == 0

    async def test_renew_exception_does_not_fence(self):
        """renew_lease raises Exception → transient error, task continues."""
        store = _FencingStore()
        runner = ExecutionRunner(max_concurrent=5, store=store, lease_ttl=3)
        blocker = asyncio.Event()

        task = await runner.submit(
            "conv-1", "t1", _blocking_factory(blocker),
            user_id="u1", stream_transport=_mock_transport,
        )

        # Make renew raise a transient error
        store._renew_error = ConnectionError("redis gone")
        await asyncio.sleep(1.5)  # past one heartbeat interval

        # Task should still be running (not fenced)
        assert runner.active_task_count == 1

        # Clean up
        store._renew_error = None
        blocker.set()
        await runner.shutdown(timeout=2)

    async def test_renew_true_task_continues(self):
        """renew_lease returns True → task keeps running normally."""
        store = _FencingStore()
        runner = ExecutionRunner(max_concurrent=5, store=store, lease_ttl=3)
        blocker = asyncio.Event()

        await runner.submit(
            "conv-1", "t1", _blocking_factory(blocker),
            user_id="u1", stream_transport=_mock_transport,
        )

        # Let heartbeat run a couple times
        await asyncio.sleep(2.5)

        # Task should still be running
        assert runner.active_task_count == 1

        blocker.set()
        await runner.shutdown(timeout=2)

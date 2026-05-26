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
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def create_stream(self, stream_id, owner_user_id=None, lease_check_key=None, lease_expected_owner=None): pass
    async def close_stream(self, stream_id): return True
    async def push_event(self, stream_id, event):
        self.events.append((stream_id, event))
        return True


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

    async def test_emits_execution_queued_when_semaphore_full(self):
        """3rd task with max_concurrent=2 should receive an execution_queued event."""
        transport = _MockStreamTransport()
        runner = ExecutionRunner(max_concurrent=2)
        release = asyncio.Event()
        started1 = asyncio.Event()
        started2 = asyncio.Event()

        async def coro1():
            started1.set()
            await release.wait()

        async def coro2():
            started2.set()
            await release.wait()

        await runner.submit("c1", "t1", coro1, user_id="u", stream_transport=transport)
        await runner.submit("c2", "t2", coro2, user_id="u", stream_transport=transport)
        await started1.wait()
        await started2.wait()

        # Pre-3rd-submit: no queued events should have been pushed (capacity available).
        assert all(ev["type"] != "execution_queued" for _, ev in transport.events)

        async def coro3():
            pass

        await runner.submit("c3", "t3", coro3, user_id="u", stream_transport=transport)
        # Give _wrapped() a tick to run the locked() check + push_event.
        await asyncio.sleep(0.05)

        queued = [(sid, ev) for sid, ev in transport.events if ev["type"] == "execution_queued"]
        assert len(queued) == 1, f"expected exactly one execution_queued event, got {queued}"
        sid, ev = queued[0]
        assert sid == "t3"
        assert ev["data"]["max_concurrent"] == 2
        # ahead = max(0, len(_tasks) - max_concurrent - 1) = max(0, 3-2-1) = 0
        assert ev["data"]["ahead"] == 0

        release.set()
        await runner.shutdown(timeout=2)

    async def test_no_execution_queued_when_capacity_available(self):
        transport = _MockStreamTransport()
        runner = ExecutionRunner(max_concurrent=5)
        await runner.submit("c1", "t1", _noop_coro, user_id="u", stream_transport=transport)
        await asyncio.sleep(0.05)
        assert all(ev["type"] != "execution_queued" for _, ev in transport.events)
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
        started = asyncio.Event()
        completed = asyncio.Event()

        async def slow_coro():
            started.set()
            await asyncio.sleep(0.1)
            completed.set()

        await runner.submit("conv-1", "t1", slow_coro, user_id="u1", stream_transport=_mock_transport)
        # Ensure the task is genuinely RUNNING (past the QUEUED→RUNNING mark) before
        # shutting down — otherwise shutdown_cleanup clears the InMemory lease first and
        # the not-yet-started task correctly aborts (CAS finds no lease) instead of running.
        await started.wait()
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

    async def test_submit_acquires_lease_immediately_marks_interactive_when_running(self):
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(store=store)
        started = asyncio.Event()
        blocker = asyncio.Event()

        async def coro():
            started.set()
            await blocker.wait()

        await runner.submit("conv-1", "t1", coro, user_id="u1", stream_transport=_mock_transport)

        # lease 在 submit 内同步获取（QUEUED 起就持有）→ 立即可见
        assert await store.get_leased_message_id("conv-1") == "t1"

        # interactive 标记在取得 semaphore 之后（QUEUED→RUNNING 边）→ 等 coro 真正
        # 开始跑才可见（mark_interactive 紧挨在 `await coro` 之前）
        await started.wait()
        assert await store.get_interactive_message_id("conv-1") == "t1"

        blocker.set()
        await runner.shutdown(timeout=2)

    async def test_queued_turn_marks_interactive_only_after_acquiring_slot(self):
        """排队态与运行态分离：QUEUED = 持 lease 但未 interactive；取得槽位后才 interactive。"""
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(max_concurrent=1, store=store)
        b1 = asyncio.Event()
        b2 = asyncio.Event()
        t1_started = asyncio.Event()
        t2_started = asyncio.Event()

        async def coro1():
            t1_started.set()
            await b1.wait()

        async def coro2():
            t2_started.set()
            await b2.wait()

        # t1 占住唯一槽位并运行 → RUNNING
        await runner.submit("conv-1", "t1", coro1, user_id="u1", stream_transport=_mock_transport)
        await t1_started.wait()
        assert await store.get_interactive_message_id("conv-1") == "t1"

        # t2 排在 semaphore 后面（不同会话 → 独立 lease）
        await runner.submit("conv-2", "t2", coro2, user_id="u1", stream_transport=_mock_transport)
        await asyncio.sleep(0.05)

        # t2 处于 QUEUED：lease 已持有，但 interactive 未标记（引擎尚未起跑）
        assert not t2_started.is_set()
        assert await store.get_leased_message_id("conv-2") == "t2"
        assert await store.get_interactive_message_id("conv-2") is None

        # 释放 t1 → t2 取得槽位 → 进入 RUNNING → interactive 此时才标记
        b1.set()
        await t2_started.wait()
        assert await store.get_interactive_message_id("conv-2") == "t2"

        b2.set()
        await runner.shutdown(timeout=2)

    async def test_queued_turn_aborts_if_lease_lost_before_running(self):
        """排队中丢了 lease 的旧 task 取得槽位后必须 abort：不跑、不覆盖新 owner 的 interactive。"""
        store = InMemoryRuntimeStore()
        runner = ExecutionRunner(max_concurrent=1, store=store)
        b1 = asyncio.Event()
        t1_started = asyncio.Event()
        t2_ran = asyncio.Event()

        async def coro1():
            t1_started.set()
            await b1.wait()

        async def coro2():
            t2_ran.set()  # 绝不应运行

        # t1 占住唯一槽位
        await runner.submit("conv-1", "t1", coro1, user_id="u1", stream_transport=_mock_transport)
        await t1_started.wait()

        # t2 排在 semaphore 后面
        await runner.submit("conv-2", "t2", coro2, user_id="u1", stream_transport=_mock_transport)
        await asyncio.sleep(0.05)
        assert await store.get_leased_message_id("conv-2") == "t2"

        # 模拟 t2 排队期间丢了 lease：新一轮 t2-new 接管 conv-2（lease 过期 + 被抢）
        store._conversation_leases["conv-2"] = "t2-new"
        assert await store.mark_engine_interactive("conv-2", "t2-new") is True

        # 释放槽位 → t2 取得 semaphore，但发现已不持有 lease → abort（不跑 coro、不覆盖 interactive）
        b1.set()
        await asyncio.sleep(0.1)

        assert not t2_ran.is_set(), "丢了 lease 的旧 t2 不应运行"
        assert await store.get_interactive_message_id("conv-2") == "t2-new", \
            "t2 不应覆盖新 owner 的 interactive key"
        assert "t2" not in runner._tasks, "t2 应已清理"
        # 新 owner 的 lease 未被 t2 的 cleanup（compare-and-del）误删
        assert await store.get_leased_message_id("conv-2") == "t2-new"

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
            async def create_stream(self, stream_id, owner_user_id=None, lease_check_key=None, lease_expected_owner=None):
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
        assert await transport.get_stream_status("t1") != "pending"


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

    async def test_renew_false_during_semaphore_wait_cleans_up(self):
        """Lease lost while task is queued for semaphore → cleanup still runs."""
        store = _FencingStore()
        runner = ExecutionRunner(max_concurrent=1, store=store, lease_ttl=3)
        blocker = asyncio.Event()

        # t1 holds the only semaphore slot
        await runner.submit(
            "conv-1", "t1", _blocking_factory(blocker),
            user_id="u1", stream_transport=_mock_transport,
        )

        # t2 is queued waiting for semaphore — coro never starts
        coro_started = asyncio.Event()

        async def coro2():
            coro_started.set()
            await asyncio.Event().wait()

        await runner.submit(
            "conv-2", "t2", coro2,
            user_id="u1", stream_transport=_mock_transport,
        )
        await asyncio.sleep(0.05)
        assert not coro_started.is_set(), "t2 should be waiting for semaphore"

        # Fence t2 while it's still queued
        store._renew_result = False
        await asyncio.sleep(1.5)  # heartbeat fires, finds lease lost

        # t2 should be cleaned up despite never executing
        assert "t2" not in runner._tasks, "_tasks should not have stale t2 entry"
        assert await store.get_leased_message_id("conv-2") is None

        # Release t1 and shut down
        blocker.set()
        await runner.shutdown(timeout=2)

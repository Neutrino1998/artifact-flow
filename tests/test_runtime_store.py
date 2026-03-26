"""
RuntimeStore unit tests.

Pure asyncio — no DB, no LLM mocks needed.
"""

import asyncio

import pytest

from api.services.runtime_store import InMemoryRuntimeStore, _InterruptState


# ============================================================
# TestLease
# ============================================================


class TestLease:

    async def test_acquire_success(self):
        store = InMemoryRuntimeStore()
        result = await store.try_acquire_lease("conv-1", "msg-1")
        assert result is None  # success

    async def test_duplicate_acquire(self):
        store = InMemoryRuntimeStore()
        await store.try_acquire_lease("conv-1", "msg-1")
        result = await store.try_acquire_lease("conv-1", "msg-2")
        assert result == "msg-1"  # already leased

    async def test_release_allows_re_acquire(self):
        store = InMemoryRuntimeStore()
        await store.try_acquire_lease("conv-1", "msg-1")
        await store.release_lease("conv-1", "msg-1")
        result = await store.try_acquire_lease("conv-1", "msg-2")
        assert result is None

    async def test_get_leased_message_id(self):
        store = InMemoryRuntimeStore()
        assert await store.get_leased_message_id("conv-1") is None

        await store.try_acquire_lease("conv-1", "msg-1")
        assert await store.get_leased_message_id("conv-1") == "msg-1"

    async def test_release_nonexistent_is_safe(self):
        store = InMemoryRuntimeStore()
        await store.release_lease("conv-x", "msg-x")  # should not raise


# ============================================================
# TestEngineInteractive
# ============================================================


class TestEngineInteractive:

    async def test_mark_and_get(self):
        store = InMemoryRuntimeStore()
        assert await store.get_interactive_message_id("conv-1") is None

        await store.mark_engine_interactive("conv-1", "msg-1")
        assert await store.get_interactive_message_id("conv-1") == "msg-1"

    async def test_clear(self):
        store = InMemoryRuntimeStore()
        await store.mark_engine_interactive("conv-1", "msg-1")
        await store.clear_engine_interactive("conv-1", "msg-1")
        assert await store.get_interactive_message_id("conv-1") is None

    async def test_clear_nonexistent_is_safe(self):
        store = InMemoryRuntimeStore()
        await store.clear_engine_interactive("conv-x", "msg-x")  # should not raise


# ============================================================
# TestInterrupt
# ============================================================


class TestInterrupt:

    async def test_wait_for_interrupt_resolve(self):
        """wait_for_interrupt blocks until resolved, then returns resume_data."""
        store = InMemoryRuntimeStore()

        async def _resolve_later():
            await asyncio.sleep(0.05)
            await store.resolve_interrupt("msg-1", {"approved": True})

        asyncio.create_task(_resolve_later())
        result = await store.wait_for_interrupt("msg-1", {"tool": "web_search"}, timeout=2.0)
        assert result == {"approved": True}

    async def test_wait_for_interrupt_timeout(self):
        """wait_for_interrupt returns None on timeout."""
        store = InMemoryRuntimeStore()
        result = await store.wait_for_interrupt("msg-1", {"tool": "test"}, timeout=0.01)
        assert result is None

    async def test_resolve_nonexistent_returns_not_found(self):
        store = InMemoryRuntimeStore()
        result = await store.resolve_interrupt("msg-x", {"approved": True})
        assert result == "not_found"

    async def test_resolve_already_resolved(self):
        store = InMemoryRuntimeStore()
        # Set up interrupt directly (bypass wait_for_interrupt which blocks)
        store._interrupts["msg-1"] = _InterruptState(interrupt_data={})
        await store.resolve_interrupt("msg-1", {"approved": True})
        result = await store.resolve_interrupt("msg-1", {"approved": False})
        assert result == "already_resolved"

    async def test_get_interrupt_data(self):
        store = InMemoryRuntimeStore()
        # Set up interrupt directly
        store._interrupts["msg-1"] = _InterruptState(interrupt_data={"tool": "test"})

        data = await store.get_interrupt_data("msg-1")
        assert data is not None
        assert data == {"tool": "test"}

        assert await store.get_interrupt_data("nonexistent") is None

    async def test_wait_for_interrupt_creates_interrupt_data(self):
        """wait_for_interrupt should make interrupt_data visible via get_interrupt_data."""
        store = InMemoryRuntimeStore()

        async def _resolve_later():
            # Wait until interrupt is created, then check data + resolve
            for _ in range(100):
                data = await store.get_interrupt_data("msg-1")
                if data is not None:
                    assert data == {"tool": "test"}
                    await store.resolve_interrupt("msg-1", {"approved": True})
                    return
                await asyncio.sleep(0.01)

        asyncio.create_task(_resolve_later())
        result = await store.wait_for_interrupt("msg-1", {"tool": "test"}, timeout=2.0)
        assert result == {"approved": True}


# ============================================================
# TestCancellation
# ============================================================


class TestCancellation:

    async def test_request_cancel(self):
        store = InMemoryRuntimeStore()
        await store.request_cancel("msg-1")
        assert await store.is_cancelled("msg-1") is True

    async def test_is_cancelled_no_event(self):
        store = InMemoryRuntimeStore()
        assert await store.is_cancelled("msg-x") is False

    async def test_cancel_wakes_pending_interrupt(self):
        store = InMemoryRuntimeStore()
        # Set up interrupt directly
        store._interrupts["msg-1"] = _InterruptState(interrupt_data={"tool": "x"})

        await store.request_cancel("msg-1")

        # Interrupt should be resolved with cancel data
        interrupt = store._interrupts["msg-1"]
        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": False, "reason": "cancelled"}


# ============================================================
# TestMessageQueue
# ============================================================


class TestMessageQueue:

    async def test_inject_and_drain(self):
        store = InMemoryRuntimeStore()
        await store.inject_message("msg-1", "hello")
        await store.inject_message("msg-1", "world")
        await store.inject_message("msg-1", "!")

        messages = await store.drain_messages("msg-1")
        assert messages == ["hello", "world", "!"]

    async def test_drain_empty(self):
        store = InMemoryRuntimeStore()
        assert await store.drain_messages("msg-x") == []

    async def test_inject_auto_creates_queue(self):
        store = InMemoryRuntimeStore()
        assert "msg-1" not in store._queues
        await store.inject_message("msg-1", "auto")
        assert "msg-1" in store._queues


# ============================================================
# TestCleanup
# ============================================================


class TestCleanup:

    async def test_cleanup_execution_clears_all_dicts(self):
        store = InMemoryRuntimeStore()
        await store.try_acquire_lease("conv-1", "msg-1")
        await store.mark_engine_interactive("conv-1", "msg-1")
        store._interrupts["msg-1"] = _InterruptState(interrupt_data={"tool": "x"})
        store._cancellations["msg-1"] = asyncio.Event()
        store._queues["msg-1"] = asyncio.Queue()

        await store.cleanup_execution("conv-1", "msg-1")

        assert await store.get_leased_message_id("conv-1") is None
        assert await store.get_interactive_message_id("conv-1") is None
        assert await store.get_interrupt_data("msg-1") is None
        assert await store.is_cancelled("msg-1") is False
        assert await store.drain_messages("msg-1") == []

    async def test_shutdown_cleanup_wakes_interrupts(self):
        store = InMemoryRuntimeStore()
        store._interrupts["msg-1"] = _InterruptState(interrupt_data={})

        interrupt = store._interrupts["msg-1"]
        assert not interrupt.event.is_set()

        await store.shutdown_cleanup()

        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": False, "reason": "shutdown"}

    async def test_shutdown_cleanup_clears_all_state(self):
        store = InMemoryRuntimeStore()
        await store.try_acquire_lease("conv-1", "msg-1")
        await store.mark_engine_interactive("conv-1", "msg-1")
        store._interrupts["msg-1"] = _InterruptState(interrupt_data={})
        store._cancellations["msg-1"] = asyncio.Event()
        store._queues["msg-1"] = asyncio.Queue()

        await store.shutdown_cleanup()

        assert len(store._conversation_leases) == 0
        assert len(store._engine_interactive) == 0
        assert len(store._interrupts) == 0
        assert len(store._cancellations) == 0
        assert len(store._queues) == 0

    async def test_renew_lease_is_noop(self):
        store = InMemoryRuntimeStore()
        await store.renew_lease("conv-1", "msg-1", 30.0)  # should not raise

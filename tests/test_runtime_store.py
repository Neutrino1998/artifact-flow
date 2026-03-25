"""
RuntimeStore unit tests.

Pure asyncio — no DB, no LLM mocks needed.
"""

import asyncio

import pytest

from api.services.runtime_store import InMemoryRuntimeStore
from core.engine import InterruptState


# ============================================================
# TestLease
# ============================================================


class TestLease:

    async def test_acquire_success(self):
        store = InMemoryRuntimeStore()
        result = store.try_acquire_lease("conv-1", "msg-1")
        assert result is None  # success

    async def test_duplicate_acquire(self):
        store = InMemoryRuntimeStore()
        store.try_acquire_lease("conv-1", "msg-1")
        result = store.try_acquire_lease("conv-1", "msg-2")
        assert result == "msg-1"  # already leased

    async def test_release_allows_re_acquire(self):
        store = InMemoryRuntimeStore()
        store.try_acquire_lease("conv-1", "msg-1")
        store.release_lease("conv-1")
        result = store.try_acquire_lease("conv-1", "msg-2")
        assert result is None

    async def test_get_leased_message_id(self):
        store = InMemoryRuntimeStore()
        assert store.get_leased_message_id("conv-1") is None

        store.try_acquire_lease("conv-1", "msg-1")
        assert store.get_leased_message_id("conv-1") == "msg-1"

    async def test_release_nonexistent_is_safe(self):
        store = InMemoryRuntimeStore()
        store.release_lease("conv-x")  # should not raise


# ============================================================
# TestEngineInteractive
# ============================================================


class TestEngineInteractive:

    async def test_mark_and_get(self):
        store = InMemoryRuntimeStore()
        assert store.get_interactive_message_id("conv-1") is None

        store.mark_engine_interactive("conv-1", "msg-1")
        assert store.get_interactive_message_id("conv-1") == "msg-1"

    async def test_clear(self):
        store = InMemoryRuntimeStore()
        store.mark_engine_interactive("conv-1", "msg-1")
        store.clear_engine_interactive("conv-1")
        assert store.get_interactive_message_id("conv-1") is None

    async def test_clear_nonexistent_is_safe(self):
        store = InMemoryRuntimeStore()
        store.clear_engine_interactive("conv-x")  # should not raise


# ============================================================
# TestInterrupt
# ============================================================


class TestInterrupt:

    async def test_create_and_resolve(self):
        store = InMemoryRuntimeStore()
        interrupt = store.create_interrupt("msg-1", {"tool": "web_search"})
        assert isinstance(interrupt, InterruptState)
        assert not interrupt.event.is_set()

        result = store.resolve_interrupt("msg-1", {"approved": True})
        assert result == "resolved"
        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": True}

    async def test_resolve_nonexistent_returns_not_found(self):
        store = InMemoryRuntimeStore()
        result = store.resolve_interrupt("msg-x", {"approved": True})
        assert result == "not_found"

    async def test_resolve_already_resolved(self):
        store = InMemoryRuntimeStore()
        store.create_interrupt("msg-1", {})
        store.resolve_interrupt("msg-1", {"approved": True})
        result = store.resolve_interrupt("msg-1", {"approved": False})
        assert result == "already_resolved"

    async def test_get_interrupt(self):
        store = InMemoryRuntimeStore()
        store.create_interrupt("msg-1", {"tool": "test"})

        interrupt = store.get_interrupt("msg-1")
        assert interrupt is not None
        assert interrupt.interrupt_data == {"tool": "test"}

        assert store.get_interrupt("nonexistent") is None


# ============================================================
# TestCancellation
# ============================================================


class TestCancellation:

    async def test_request_cancel(self):
        store = InMemoryRuntimeStore()
        store.request_cancel("msg-1")
        assert store.is_cancelled("msg-1") is True

    async def test_is_cancelled_no_event(self):
        store = InMemoryRuntimeStore()
        assert store.is_cancelled("msg-x") is False

    async def test_cancel_wakes_pending_interrupt(self):
        store = InMemoryRuntimeStore()
        interrupt = store.create_interrupt("msg-1", {"tool": "x"})
        assert not interrupt.event.is_set()

        store.request_cancel("msg-1")
        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": False, "reason": "cancelled"}


# ============================================================
# TestMessageQueue
# ============================================================


class TestMessageQueue:

    async def test_inject_and_drain(self):
        store = InMemoryRuntimeStore()
        store.inject_message("msg-1", "hello")
        store.inject_message("msg-1", "world")
        store.inject_message("msg-1", "!")

        messages = store.drain_messages("msg-1")
        assert messages == ["hello", "world", "!"]

    async def test_drain_empty(self):
        store = InMemoryRuntimeStore()
        assert store.drain_messages("msg-x") == []

    async def test_inject_auto_creates_queue(self):
        store = InMemoryRuntimeStore()
        assert "msg-1" not in store._queues
        store.inject_message("msg-1", "auto")
        assert "msg-1" in store._queues


# ============================================================
# TestCleanup
# ============================================================


class TestCleanup:

    async def test_cleanup_execution_clears_all_dicts(self):
        store = InMemoryRuntimeStore()
        store.try_acquire_lease("conv-1", "msg-1")
        store.mark_engine_interactive("conv-1", "msg-1")
        store.create_interrupt("msg-1", {"tool": "x"})
        store._cancellations["msg-1"] = asyncio.Event()
        store._queues["msg-1"] = asyncio.Queue()

        store.cleanup_execution("msg-1")

        assert store.get_leased_message_id("conv-1") is None
        assert store.get_interactive_message_id("conv-1") is None
        assert store.get_interrupt("msg-1") is None
        assert store.is_cancelled("msg-1") is False
        assert store.drain_messages("msg-1") == []

    async def test_shutdown_cleanup_wakes_interrupts(self):
        store = InMemoryRuntimeStore()
        interrupt = store.create_interrupt("msg-1", {})
        assert not interrupt.event.is_set()

        store.shutdown_cleanup()

        assert interrupt.event.is_set()
        assert interrupt.resume_data == {"approved": False, "reason": "shutdown"}

    async def test_shutdown_cleanup_clears_all_state(self):
        store = InMemoryRuntimeStore()
        store.try_acquire_lease("conv-1", "msg-1")
        store.mark_engine_interactive("conv-1", "msg-1")
        store.create_interrupt("msg-1", {})
        store._cancellations["msg-1"] = asyncio.Event()
        store._queues["msg-1"] = asyncio.Queue()

        store.shutdown_cleanup()

        assert len(store._conversation_leases) == 0
        assert len(store._engine_interactive) == 0
        assert len(store._interrupts) == 0
        assert len(store._cancellations) == 0
        assert len(store._queues) == 0

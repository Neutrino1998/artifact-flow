"""ConversationLeaseHandle acquisition, heartbeat, fencing, and CAS release."""

import asyncio

import pytest

from api.services.conversation_lease import (
    ConversationLeaseConflict,
    ConversationLeaseCoordinator,
    ConversationLeaseLost,
    ConversationLeaseUnavailable,
)
from api.services.runtime_store import InMemoryRuntimeStore


class _ControllableStore(InMemoryRuntimeStore):
    def __init__(self):
        super().__init__()
        self.renewed = asyncio.Event()
        self.renew_result = True
        self.renew_error: Exception | None = None

    async def renew_lease(self, conversation_id, message_id, ttl):
        self.renewed.set()
        if self.renew_error is not None:
            raise self.renew_error
        return self.renew_result and await super().renew_lease(
            conversation_id, message_id, ttl
        )


async def test_acquire_starts_heartbeat_before_handoff():
    store = _ControllableStore()
    coordinator = ConversationLeaseCoordinator(
        store, lease_ttl=1, heartbeat_interval=0.001
    )
    handle = await coordinator.acquire("conv-1", "msg-1")
    await asyncio.wait_for(store.renewed.wait(), timeout=1)
    assert await store.get_leased_message_id("conv-1") == "msg-1"
    await handle.release()


async def test_conflict_reports_current_owner():
    store = InMemoryRuntimeStore()
    coordinator = ConversationLeaseCoordinator(store, lease_ttl=0)
    first = await coordinator.acquire("conv-1", "msg-1")
    with pytest.raises(ConversationLeaseConflict) as exc:
        await coordinator.acquire("conv-1", "msg-2")
    assert exc.value.active_owner == "msg-1"
    await first.release()


async def test_renew_false_fences_bound_task():
    store = _ControllableStore()
    coordinator = ConversationLeaseCoordinator(
        store, lease_ttl=1, heartbeat_interval=0.001
    )
    handle = await coordinator.acquire("conv-1", "msg-1")
    fenced = asyncio.Event()
    handle.bind_fence(fenced.set)
    store.renew_result = False
    await asyncio.wait_for(fenced.wait(), timeout=1)
    with pytest.raises(ConversationLeaseLost):
        handle.ensure_owned()
    await handle.release()


async def test_renew_exception_fences_fail_closed():
    store = _ControllableStore()
    store.renew_error = ConnectionError("redis unavailable")
    coordinator = ConversationLeaseCoordinator(
        store, lease_ttl=1, heartbeat_interval=0.001
    )
    handle = await coordinator.acquire("conv-1", "msg-1")
    fenced = asyncio.Event()
    handle.bind_fence(fenced.set)
    await asyncio.wait_for(fenced.wait(), timeout=1)
    with pytest.raises(ConversationLeaseLost):
        handle.ensure_owned()
    await handle.release()


async def test_stale_handle_release_does_not_clear_replacement_owner():
    store = InMemoryRuntimeStore()
    coordinator = ConversationLeaseCoordinator(store, lease_ttl=0)
    old = await coordinator.acquire("conv-1", "msg-old")
    store._conversation_leases["conv-1"] = "msg-new"
    await old.release()
    assert await store.get_leased_message_id("conv-1") == "msg-new"


async def test_loss_before_bind_fences_immediately():
    store = _ControllableStore()
    store.renew_result = False
    coordinator = ConversationLeaseCoordinator(
        store, lease_ttl=1, heartbeat_interval=0.001
    )
    handle = await coordinator.acquire("conv-1", "msg-1")
    while not handle.lost:
        await asyncio.sleep(0)
    fenced = asyncio.Event()
    handle.bind_fence(fenced.set)
    assert fenced.is_set()
    await handle.release()


async def test_indeterminate_acquire_fails_closed_and_attempts_cas_release():
    store = InMemoryRuntimeStore()
    released: list[tuple[str, str]] = []

    async def indeterminate_acquire(conversation_id, owner_id):
        store._conversation_leases[conversation_id] = owner_id
        raise ConnectionError("response lost after commit")

    original_release = store.release_lease

    async def recording_release(conversation_id, owner_id):
        released.append((conversation_id, owner_id))
        await original_release(conversation_id, owner_id)

    store.try_acquire_lease = indeterminate_acquire
    store.release_lease = recording_release
    coordinator = ConversationLeaseCoordinator(store, lease_ttl=30)

    with pytest.raises(ConversationLeaseUnavailable):
        await coordinator.acquire("conv-1", "msg-1")

    assert released == [("conv-1", "msg-1")]
    assert await store.get_leased_message_id("conv-1") is None

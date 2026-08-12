"""Conversation admission heartbeat, handoff, fencing, and cleanup contracts."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

from api.dependencies import (
    get_conversation_execution_service,
    get_runtime_store,
    get_task_supervisor,
)
from api.services.conversation_lease import ConversationLeaseCoordinator
from core.conversation_manager import ConversationManager
from db.models import User
from repositories.conversation_repo import ConversationRepository


async def _seed_conversation(db_manager, user_id: str) -> str:
    conversation_id = f"conv-{uuid.uuid4().hex}"
    async with db_manager.session() as session:
        await ConversationRepository(session).create_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
        )
    return conversation_id


def _chat_payload(conversation_id: str) -> dict:
    return {
        "payload": (
            None,
            json.dumps({
                "user_input": "continue",
                "conversation_id": conversation_id,
            }),
        ),
    }


async def test_heartbeat_runs_while_send_admission_is_blocked(
    client, app, db_manager, test_user: User, monkeypatch
):
    conversation_id = await _seed_conversation(db_manager, test_user.id)
    service = app.dependency_overrides[get_conversation_execution_service]()
    store = app.dependency_overrides[get_runtime_store]()
    service._leases = ConversationLeaseCoordinator(
        store, lease_ttl=1, heartbeat_interval=0.001
    )

    renewed = asyncio.Event()
    original_renew = store.renew_lease

    async def recording_renew(conv_id, message_id, ttl):
        renewed.set()
        return await original_renew(conv_id, message_id, ttl)

    monkeypatch.setattr(store, "renew_lease", recording_renew)

    reached_final_require = asyncio.Event()
    continue_admission = asyncio.Event()
    original_require = ConversationManager.require_owned
    require_calls = 0

    async def blocked_require(manager, conv_id, user_id=None):
        nonlocal require_calls
        if conv_id == conversation_id:
            require_calls += 1
            if require_calls == 2:
                reached_final_require.set()
                await continue_admission.wait()
        return await original_require(manager, conv_id, user_id=user_id)

    async def no_op_execute(**_kwargs):
        return None

    monkeypatch.setattr(service, "_execute_and_push", no_op_execute)
    with patch.object(ConversationManager, "require_owned", new=blocked_require):
        send = asyncio.create_task(
            client.post("/api/v1/chat", files=_chat_payload(conversation_id))
        )
        await asyncio.wait_for(reached_final_require.wait(), timeout=2)
        await asyncio.wait_for(renewed.wait(), timeout=2)
        continue_admission.set()
        response = await send

    assert response.status_code == 200
    await service.shutdown(timeout=1)


async def test_heartbeat_runs_while_delete_is_blocked(
    client, app, db_manager, test_user: User, monkeypatch
):
    conversation_id = await _seed_conversation(db_manager, test_user.id)
    service = app.dependency_overrides[get_conversation_execution_service]()
    store = app.dependency_overrides[get_runtime_store]()
    service._leases = ConversationLeaseCoordinator(
        store, lease_ttl=1, heartbeat_interval=0.001
    )

    renewed = asyncio.Event()
    original_renew = store.renew_lease

    async def recording_renew(conv_id, message_id, ttl):
        renewed.set()
        return await original_renew(conv_id, message_id, ttl)

    monkeypatch.setattr(store, "renew_lease", recording_renew)

    delete_started = asyncio.Event()
    continue_delete = asyncio.Event()
    original_delete = ConversationManager.delete_conversation

    async def blocked_delete(manager, conv_id):
        if conv_id == conversation_id:
            delete_started.set()
            await continue_delete.wait()
        return await original_delete(manager, conv_id)

    with patch.object(
        ConversationManager, "delete_conversation", new=blocked_delete
    ):
        deletion = asyncio.create_task(
            client.delete(f"/api/v1/chat/{conversation_id}")
        )
        await asyncio.wait_for(delete_started.wait(), timeout=2)
        await asyncio.wait_for(renewed.wait(), timeout=2)
        continue_delete.set()
        response = await deletion

    assert response.status_code == 200


async def test_submit_hands_off_one_lease_and_cleans_lifo(
    client, app, db_manager, test_user: User, monkeypatch
):
    conversation_id = await _seed_conversation(db_manager, test_user.id)
    service = app.dependency_overrides[get_conversation_execution_service]()
    store = app.dependency_overrides[get_runtime_store]()
    supervisor = app.dependency_overrides[get_task_supervisor]()
    streams = service._streams

    acquired: list[str] = []
    order: list[str] = []
    released = asyncio.Event()
    original_acquire = store.try_acquire_lease
    original_clear = store.clear_engine_interactive
    original_message_cleanup = store.cleanup_message_state
    original_close = streams.close_stream
    original_release = store.release_lease

    async def recording_acquire(conv_id, owner_id):
        if conv_id == conversation_id:
            acquired.append(owner_id)
        return await original_acquire(conv_id, owner_id)

    async def recording_clear(conv_id, message_id):
        order.append("interactive")
        await original_clear(conv_id, message_id)

    async def recording_message_cleanup(message_id):
        order.append("message")
        await original_message_cleanup(message_id)

    async def recording_close(message_id):
        order.append("stream")
        return await original_close(message_id)

    async def recording_release(conv_id, owner_id):
        order.append("lease")
        await original_release(conv_id, owner_id)
        released.set()

    monkeypatch.setattr(store, "try_acquire_lease", recording_acquire)
    monkeypatch.setattr(store, "clear_engine_interactive", recording_clear)
    monkeypatch.setattr(store, "cleanup_message_state", recording_message_cleanup)
    monkeypatch.setattr(streams, "close_stream", recording_close)
    monkeypatch.setattr(store, "release_lease", recording_release)

    async def fake_execute(*, scope, **_kwargs):
        async def close_sandbox():
            order.append("sandbox")
        scope.add_cleanup("sandbox session", close_sandbox)

    monkeypatch.setattr(service, "_execute_and_push", fake_execute)

    response = await client.post(
        "/api/v1/chat", files=_chat_payload(conversation_id)
    )
    assert response.status_code == 200
    await asyncio.wait_for(released.wait(), timeout=2)
    await supervisor.shutdown(timeout=1)

    assert len(acquired) == 1
    assert not acquired[0].startswith("delete:")
    assert order == ["sandbox", "interactive", "message", "stream", "lease"]


async def test_fencing_cancels_admitted_task_and_runs_cleanup(
    client, app, db_manager, test_user: User, monkeypatch
):
    conversation_id = await _seed_conversation(db_manager, test_user.id)
    service = app.dependency_overrides[get_conversation_execution_service]()
    store = app.dependency_overrides[get_runtime_store]()
    streams = service._streams
    service._leases = ConversationLeaseCoordinator(
        store, lease_ttl=1, heartbeat_interval=0.001
    )

    allow_renew = True
    original_renew = store.renew_lease

    async def controlled_renew(conv_id, message_id, ttl):
        if not allow_renew:
            return False
        return await original_renew(conv_id, message_id, ttl)

    monkeypatch.setattr(store, "renew_lease", controlled_renew)
    workload_started = asyncio.Event()
    release_finished = asyncio.Event()
    original_release = store.release_lease

    async def recording_release(conv_id, owner_id):
        await original_release(conv_id, owner_id)
        release_finished.set()

    monkeypatch.setattr(store, "release_lease", recording_release)

    async def blocking_execute(**_kwargs):
        workload_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_execute_and_push", blocking_execute)

    response = await client.post(
        "/api/v1/chat", files=_chat_payload(conversation_id)
    )
    assert response.status_code == 200
    await asyncio.wait_for(workload_started.wait(), timeout=2)
    allow_renew = False
    await asyncio.wait_for(release_finished.wait(), timeout=2)

    message_id = response.json()["message_id"]
    assert await store.get_leased_message_id(conversation_id) is None
    assert await store.get_interactive_message_id(conversation_id) is None
    assert await streams.get_stream_status(message_id) == "closed"


async def test_indeterminate_lease_acquire_maps_to_retryable_503(
    client, app, db_manager, test_user: User, monkeypatch
):
    conversation_id = await _seed_conversation(db_manager, test_user.id)
    store = app.dependency_overrides[get_runtime_store]()

    async def unavailable_acquire(_conversation_id, _owner_id):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(store, "try_acquire_lease", unavailable_acquire)
    response = await client.post(
        "/api/v1/chat", files=_chat_payload(conversation_id)
    )

    assert response.status_code == 503
    assert "please retry" in response.json()["detail"]


async def test_initialization_failure_is_sanitized_and_scope_closes_stream(
    client, app, db_manager, test_user: User, monkeypatch
):
    conversation_id = await _seed_conversation(db_manager, test_user.id)
    service = app.dependency_overrides[get_conversation_execution_service]()
    store = app.dependency_overrides[get_runtime_store]()
    streams = service._streams

    @asynccontextmanager
    async def broken_controller(*_args, **_kwargs):
        raise RuntimeError("secret initialization detail")
        yield  # pragma: no cover

    monkeypatch.setattr(
        "api.services.conversation_execution_service.create_controller",
        broken_controller,
    )
    monkeypatch.setattr("api.services.controller_factory.config.DEBUG", False)
    ops_log = MagicMock()
    monkeypatch.setattr(
        "api.services.conversation_execution_service.logger.exception", ops_log
    )

    response = await client.post(
        "/api/v1/chat", files=_chat_payload(conversation_id)
    )
    assert response.status_code == 200
    message_id = response.json()["message_id"]

    async def wait_until_closed():
        while await streams.get_stream_status(message_id) != "closed":
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_closed(), timeout=2)
    events = list(streams.streams[message_id].history.values())
    error = next(event for event in events if event["type"] == "error")
    assert error["data"]["error"] == "Internal server error"
    assert error["data"]["request_id"] == response.headers["x-request-id"]
    assert await store.get_leased_message_id(conversation_id) is None
    ops_log.assert_called_once()
    assert "secret initialization detail" in ops_log.call_args.args[0]

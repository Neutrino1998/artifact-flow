"""Deletion contract for conversations that still own an execution lease."""

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from api.dependencies import get_conversation_execution_service, get_runtime_store
from core.conversation_manager import ConversationManager
from db.models import Conversation, User
from repositories.conversation_repo import ConversationRepository


async def _seed_conversation(db_manager, user_id: str) -> str:
    conv_id = f"conv-{uuid.uuid4().hex}"
    async with db_manager.session() as session:
        await ConversationRepository(session).create_conversation(
            conversation_id=conv_id,
            user_id=user_id,
        )
    return conv_id


async def _exists(db_manager, conv_id: str) -> bool:
    async with db_manager.session() as session:
        result = await session.execute(
            select(Conversation.id).where(Conversation.id == conv_id)
        )
        return result.scalar_one_or_none() is not None


def _chat_payload(conv_id: str) -> dict:
    return {
        "payload": (
            None,
            json.dumps({
                "user_input": "continue",
                "conversation_id": conv_id,
            }),
        ),
    }


async def test_single_delete_rejects_queued_or_running_lease(
    client, app, db_manager, test_user: User, monkeypatch
):
    conv_id = await _seed_conversation(db_manager, test_user.id)
    store = app.dependency_overrides[get_runtime_store]()
    message_id = f"msg-{uuid.uuid4().hex}"
    await store.try_acquire_lease(conv_id, message_id)
    ops_warning = MagicMock()
    monkeypatch.setattr(
        "api.services.conversation_execution_service.logger.warning", ops_warning
    )

    try:
        response = await client.delete(f"/api/v1/chat/{conv_id}")

        assert response.status_code == 409
        assert "active execution" in response.json()["detail"]
        assert await _exists(db_manager, conv_id)
        ops_warning.assert_called_once()
        logged = ops_warning.call_args.args[0]
        assert conv_id in logged
        assert message_id in logged
    finally:
        await store.release_lease(conv_id, message_id)


async def test_bulk_delete_skips_active_and_deletes_inactive(
    client, app, db_manager, test_user: User
):
    active_id = await _seed_conversation(db_manager, test_user.id)
    inactive_id = await _seed_conversation(db_manager, test_user.id)
    store = app.dependency_overrides[get_runtime_store]()
    message_id = f"msg-{uuid.uuid4().hex}"
    await store.try_acquire_lease(active_id, message_id)

    try:
        response = await client.post(
            "/api/v1/chat/bulk-delete",
            json={"ids": [active_id, inactive_id]},
        )

        assert response.status_code == 200
        assert response.json() == {
            "deleted": [inactive_id],
            "failed": [{"id": active_id, "reason": "active_execution"}],
        }
        assert await _exists(db_manager, active_id)
        assert not await _exists(db_manager, inactive_id)
    finally:
        await store.release_lease(active_id, message_id)


async def test_delete_commit_is_not_turned_into_500_when_lease_release_fails(
    client, app, db_manager, test_user: User, monkeypatch
):
    """An irreversible DELETE stays successful while ops gets the stack."""
    conv_id = await _seed_conversation(db_manager, test_user.id)
    store = app.dependency_overrides[get_runtime_store]()
    ops_error = MagicMock()
    monkeypatch.setattr("api.services.conversation_lease.logger.exception", ops_error)

    async def fail_release(conversation_id: str, owner_id: str):
        raise ConnectionError("redis unavailable during release")

    monkeypatch.setattr(store, "release_lease", fail_release)

    response = await client.delete(f"/api/v1/chat/{conv_id}")

    assert response.status_code == 200
    assert not await _exists(db_manager, conv_id)
    ops_error.assert_called_once()
    assert conv_id in ops_error.call_args.args[0]


async def test_send_wins_lease_before_delete_returns_conflict_without_sleep(
    client, app, db_manager, test_user: User, monkeypatch
):
    """The send-side lease is the deterministic winner, so DELETE sees it.

    The test pauses ``try_acquire_lease`` *after* the send has acquired the
    lease but before admission can continue.  No scheduler sleep
    is involved: DELETE can only run after the lease is observable.
    """
    conv_id = await _seed_conversation(db_manager, test_user.id)
    store = app.dependency_overrides[get_runtime_store]()
    original_acquire = store.try_acquire_lease
    send_has_lease = asyncio.Event()
    allow_send_submit = asyncio.Event()

    async def gated_acquire(conversation_id: str, owner_id: str):
        result = await original_acquire(conversation_id, owner_id)
        if conversation_id == conv_id and not owner_id.startswith("delete:"):
            assert result is None
            send_has_lease.set()
            await allow_send_submit.wait()
        return result

    monkeypatch.setattr(store, "try_acquire_lease", gated_acquire)
    send_task = asyncio.create_task(
        client.post("/api/v1/chat", files=_chat_payload(conv_id))
    )
    try:
        await asyncio.wait_for(send_has_lease.wait(), timeout=2)

        delete_response = await client.delete(f"/api/v1/chat/{conv_id}")
        assert delete_response.status_code == 409
        assert "active execution" in delete_response.json()["detail"]
        assert await _exists(db_manager, conv_id)
    finally:
        allow_send_submit.set()

    send_response = await send_task
    assert send_response.status_code == 200
    execution_service = app.dependency_overrides[get_conversation_execution_service]()
    await execution_service.shutdown(timeout=2)


async def test_delete_wins_before_send_acquire_returns_synchronous_not_found(
    client, app, db_manager, test_user: User, monkeypatch
):
    """Delete wins after the security precheck but before send admission.

    Reaching the gated send acquire proves that all current router-side DB
    checks have completed.  DELETE then acquires/releases the same conversation
    lease and commits before send is allowed to acquire it. The authoritative
    require inside the handle then returns 404 before stream/task creation.
    """
    conv_id = await _seed_conversation(db_manager, test_user.id)
    store = app.dependency_overrides[get_runtime_store]()
    original_acquire = store.try_acquire_lease
    send_reached_acquire = asyncio.Event()
    allow_send_acquire = asyncio.Event()

    async def gated_acquire(conversation_id: str, owner_id: str):
        if conversation_id == conv_id and not owner_id.startswith("delete:"):
            send_reached_acquire.set()
            await allow_send_acquire.wait()
        return await original_acquire(conversation_id, owner_id)

    monkeypatch.setattr(store, "try_acquire_lease", gated_acquire)
    send_task = asyncio.create_task(
        client.post("/api/v1/chat", files=_chat_payload(conv_id))
    )
    try:
        await asyncio.wait_for(send_reached_acquire.wait(), timeout=2)

        delete_response = await client.delete(f"/api/v1/chat/{conv_id}")
        assert delete_response.status_code == 200
        assert not await _exists(db_manager, conv_id)
    finally:
        allow_send_acquire.set()

    send_response = await send_task
    assert send_response.status_code == 404
    assert not await _exists(db_manager, conv_id)
    assert await store.get_leased_message_id(conv_id) is None


async def test_authoritative_require_is_protected_by_send_lease(
    client, app, db_manager, test_user: User
):
    """DELETE cannot pass while send is in its handle-scoped final require."""
    conv_id = await _seed_conversation(db_manager, test_user.id)
    reached_final_require = asyncio.Event()
    continue_send = asyncio.Event()
    original_require = ConversationManager.require_owned
    require_calls = 0

    async def blocked_require(
        manager,
        conversation_id,
        user_id=None,
    ):
        nonlocal require_calls
        if conversation_id == conv_id:
            require_calls += 1
            # First call is the early ownership check. Pause only at the final
            # post-conversion require so DELETE lands in the characterized gap.
            if require_calls == 2:
                reached_final_require.set()
                await continue_send.wait()
        return await original_require(manager, conversation_id, user_id=user_id)

    with patch.object(
        ConversationManager,
        "require_owned",
        new=blocked_require,
    ):
        send_task = asyncio.create_task(
            client.post(
                "/api/v1/chat",
                files={
                    "payload": (
                        None,
                        json.dumps({
                            "user_input": "continue",
                            "conversation_id": conv_id,
                        }),
                    ),
                },
            )
        )
        try:
            await asyncio.wait_for(reached_final_require.wait(), timeout=2)
            delete_response = await client.delete(f"/api/v1/chat/{conv_id}")
            assert delete_response.status_code == 409
        finally:
            continue_send.set()

        send_response = await send_task

    assert send_response.status_code == 200
    assert await _exists(db_manager, conv_id)
    execution_service = app.dependency_overrides[get_conversation_execution_service]()
    await execution_service.shutdown(timeout=2)
    store = app.dependency_overrides[get_runtime_store]()
    assert await store.get_leased_message_id(conv_id) is None


def test_single_delete_openapi_declares_active_execution_conflict(app):
    response = app.openapi()["paths"]["/api/v1/chat/{conv_id}"]["delete"]["responses"]["409"]

    assert response["description"] == "Conversation has an active execution"
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }

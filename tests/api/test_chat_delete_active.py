"""Deletion contract for conversations that still own an execution lease."""

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from api.dependencies import get_execution_runner
from api.services.execution_runner import ExecutionRunner
from core.conversation_manager import ConversationManager
from db.models import Conversation, Message, User
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


async def _message_exists(db_manager, message_id: str) -> bool:
    async with db_manager.session() as session:
        result = await session.execute(
            select(Message.id).where(Message.id == message_id)
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
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    message_id = f"msg-{uuid.uuid4().hex}"
    await runner.store.try_acquire_lease(conv_id, message_id)
    ops_warning = MagicMock()
    monkeypatch.setattr("api.routers.chat.logger.warning", ops_warning)

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
        await runner.store.release_lease(conv_id, message_id)


async def test_bulk_delete_skips_active_and_deletes_inactive(
    client, app, db_manager, test_user: User
):
    active_id = await _seed_conversation(db_manager, test_user.id)
    inactive_id = await _seed_conversation(db_manager, test_user.id)
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    message_id = f"msg-{uuid.uuid4().hex}"
    await runner.store.try_acquire_lease(active_id, message_id)

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
        await runner.store.release_lease(active_id, message_id)


async def test_delete_commit_is_not_turned_into_500_when_lease_release_fails(
    client, app, db_manager, test_user: User, monkeypatch
):
    """An irreversible DELETE stays successful while ops gets the stack."""
    conv_id = await _seed_conversation(db_manager, test_user.id)
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    ops_error = MagicMock()
    monkeypatch.setattr("api.routers.chat.logger.exception", ops_error)

    async def fail_release(conversation_id: str, owner_id: str):
        raise ConnectionError("redis unavailable during release")

    monkeypatch.setattr(runner.store, "release_lease", fail_release)

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
    lease but before ``ExecutionRunner.submit`` can return.  No scheduler sleep
    is involved: DELETE can only run after the lease is observable.
    """
    conv_id = await _seed_conversation(db_manager, test_user.id)
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    store = runner.store
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
    await runner.shutdown(timeout=2)


async def test_delete_wins_between_send_check_and_lease_reproduces_current_gap(
    client, app, db_manager, test_user: User, monkeypatch
):
    """Characterize the current check→lease gap without timing guesses.

    Reaching the gated send acquire proves that all current router-side DB
    checks have completed.  DELETE then acquires/releases the same conversation
    lease and commits before send is allowed to acquire it.  Phase C changes
    the final send expectation from 200 to 404 by moving the authoritative
    require inside the lease; Phase A intentionally records today's reachable
    behavior while also proving the deleted row is never resurrected.
    """
    conv_id = await _seed_conversation(db_manager, test_user.id)
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    store = runner.store
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

    # Current behavior: submit succeeds after DELETE releases its lease, then
    # the background controller's no-create check aborts the turn.  C will make
    # this request fail synchronously with 404 before stream/task creation.
    send_response = await send_task
    assert send_response.status_code == 200
    message_id = send_response.json()["message_id"]

    await runner.shutdown(timeout=2)
    assert not await _exists(db_manager, conv_id)
    assert not await _message_exists(db_manager, message_id)
    assert await store.get_leased_message_id(conv_id) is None


async def test_send_cannot_resurrect_conversation_deleted_after_ownership_check(
    client, app, db_manager, test_user: User
):
    """Existing send must fail closed if DELETE wins before its final ensure."""
    conv_id = await _seed_conversation(db_manager, test_user.id)
    reached_final_ensure = asyncio.Event()
    continue_send = asyncio.Event()
    original_ensure = ConversationManager.ensure_conversation_exists

    async def blocked_ensure(
        manager,
        conversation_id,
        user_id=None,
        *,
        create_if_missing=True,
    ):
        if conversation_id == conv_id:
            reached_final_ensure.set()
            await continue_send.wait()
        return await original_ensure(
            manager,
            conversation_id,
            user_id=user_id,
            create_if_missing=create_if_missing,
        )

    with patch.object(
        ConversationManager,
        "ensure_conversation_exists",
        new=blocked_ensure,
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
            await asyncio.wait_for(reached_final_ensure.wait(), timeout=2)
            delete_response = await client.delete(f"/api/v1/chat/{conv_id}")
            assert delete_response.status_code == 200
        finally:
            continue_send.set()

        send_response = await send_task

    assert send_response.status_code == 404
    assert not await _exists(db_manager, conv_id)
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    assert await runner.store.get_leased_message_id(conv_id) is None


def test_single_delete_openapi_declares_active_execution_conflict(app):
    response = app.openapi()["paths"]["/api/v1/chat/{conv_id}"]["delete"]["responses"]["409"]

    assert response["description"] == "Conversation has an active execution"
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }

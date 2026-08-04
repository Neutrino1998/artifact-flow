"""Deletion contract for conversations that still own an execution lease."""

import uuid

from sqlalchemy import select

from api.dependencies import get_execution_runner
from api.services.execution_runner import ExecutionRunner
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


async def test_single_delete_rejects_queued_or_running_lease(
    client, app, db_manager, test_user: User
):
    conv_id = await _seed_conversation(db_manager, test_user.id)
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    message_id = f"msg-{uuid.uuid4().hex}"
    await runner.store.try_acquire_lease(conv_id, message_id)

    try:
        response = await client.delete(f"/api/v1/chat/{conv_id}")

        assert response.status_code == 409
        assert "active execution" in response.json()["detail"]
        assert await _exists(db_manager, conv_id)
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

"""Admin conversation observability: active execution discovery + live SSE."""

import uuid

import pytest
from httpx import AsyncClient

from api.dependencies import get_execution_runner, get_stream_transport
from db.database import DatabaseManager
from db.models import User
from repositories.conversation_repo import ConversationRepository


@pytest.fixture
async def observed_conversation(
    db_manager: DatabaseManager,
    test_user: User,
) -> tuple[str, str]:
    conv_id = f"conv-{uuid.uuid4().hex}"
    message_id = f"msg-{uuid.uuid4().hex}"
    async with db_manager.session() as session:
        repo = ConversationRepository(session)
        await repo.create_conversation(
            conversation_id=conv_id,
            title="Observed conversation",
            user_id=test_user.id,
        )
        await repo.add_message(
            conversation_id=conv_id,
            message_id=message_id,
            user_input="watch me",
            metadata={
                "uploaded_files": [
                    {"id": "brief.docx", "filename": "Brief.docx"},
                ],
            },
        )
    return conv_id, message_id


async def _make_active(app, conv_id: str, message_id: str, owner_id: str):
    runner = app.dependency_overrides[get_execution_runner]()
    transport = app.dependency_overrides[get_stream_transport]()
    assert await runner.store.try_acquire_lease(conv_id, message_id) is None
    await transport.create_stream(message_id, owner_user_id=owner_id)
    return runner, transport


class TestAdminConversationActivity:
    async def test_list_and_detail_expose_active_message_id(
        self,
        app,
        admin_client: AsyncClient,
        test_user: User,
        observed_conversation: tuple[str, str],
    ):
        conv_id, message_id = observed_conversation
        runner, transport = await _make_active(app, conv_id, message_id, test_user.id)
        try:
            listing = await admin_client.get("/api/v1/admin/conversations")
            assert listing.status_code == 200
            summary = next(
                item
                for item in listing.json()["conversations"]
                if item["id"] == conv_id
            )
            assert summary["is_active"] is True
            assert summary["active_message_id"] == message_id

            detail = await admin_client.get(f"/api/v1/admin/conversations/{conv_id}/events")
            assert detail.status_code == 200
            assert detail.json()["is_active"] is True
            assert detail.json()["active_message_id"] == message_id
            assert detail.json()["messages"][0]["uploaded_files"] == [
                {"id": "brief.docx", "filename": "Brief.docx"},
            ]
        finally:
            await transport.close_stream(message_id)
            await runner.store.release_lease(conv_id, message_id)

    async def test_admin_can_subscribe_to_owner_stream(
        self,
        app,
        admin_client: AsyncClient,
        test_user: User,
        observed_conversation: tuple[str, str],
    ):
        conv_id, message_id = observed_conversation
        runner, transport = await _make_active(app, conv_id, message_id, test_user.id)
        await transport.push_event(message_id, {
            "type": "tool_start",
            "timestamp": "2026-07-13T00:00:00",
            "agent": "lead_agent",
            "data": {"tool": "fetch"},
        })
        await transport.push_event(message_id, {
            "type": "complete",
            "timestamp": "2026-07-13T00:00:01",
            "data": {"success": True, "message_id": message_id},
        })
        try:
            response = await admin_client.get(
                f"/api/v1/admin/conversations/{conv_id}/stream"
            )
            assert response.status_code == 200
            assert "event: tool_start" in response.text
            assert "event: complete" in response.text
        finally:
            await transport.close_stream(message_id)
            await runner.store.release_lease(conv_id, message_id)

    async def test_regular_user_cannot_open_admin_stream(
        self,
        client: AsyncClient,
        observed_conversation: tuple[str, str],
    ):
        conv_id, _ = observed_conversation
        response = await client.get(f"/api/v1/admin/conversations/{conv_id}/stream")
        assert response.status_code == 403

    async def test_inactive_conversation_has_no_admin_stream(
        self,
        admin_client: AsyncClient,
        observed_conversation: tuple[str, str],
    ):
        conv_id, _ = observed_conversation
        response = await admin_client.get(f"/api/v1/admin/conversations/{conv_id}/stream")
        assert response.status_code == 404

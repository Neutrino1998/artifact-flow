"""Message feedback write path and admin read-only browser."""

from datetime import datetime
import uuid

from httpx import AsyncClient
from sqlalchemy import update

from config import config
from db.database import DatabaseManager
from db.models import MessageFeedback, User
from repositories.conversation_repo import ConversationRepository
from repositories.message_feedback_repo import MessageFeedbackRepository


async def _seed_message(
    db_manager: DatabaseManager,
    user_id: str,
    *,
    title: str = "Feedback conversation",
    message_id: str | None = None,
) -> tuple[str, str]:
    conv_id = f"conv-{uuid.uuid4().hex}"
    msg_id = message_id or f"msg-{uuid.uuid4().hex}"
    async with db_manager.session() as session:
        repo = ConversationRepository(session)
        await repo.create_conversation(conv_id, title=title, user_id=user_id)
        await repo.add_message(conv_id, msg_id, "please review this")
        await repo.update_response(msg_id, "assistant response")
    return conv_id, msg_id


class TestMessageFeedbackWrite:
    async def test_create_replace_read_and_delete(
        self,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
    ):
        conv_id, msg_id = await _seed_message(db_manager, test_user.id)
        url = f"/api/v1/chat/{conv_id}/messages/{msg_id}/feedback"

        created = await client.put(url, json={
            "rating": "positive",
            "tags": ["resolved_problem", "high_quality"],
            "detail": "  useful explanation  ",
        })
        assert created.status_code == 200
        assert created.json()["rating"] == "positive"
        assert created.json()["tags"] == ["resolved_problem", "high_quality"]
        assert created.json()["detail"] == "useful explanation"

        detail = await client.get(f"/api/v1/chat/{conv_id}")
        assert detail.status_code == 200
        assert detail.json()["messages"][0]["feedback"]["rating"] == "positive"

        invalid = await client.put(url, json={
            "rating": "negative",
            "tags": ["resolved_problem"],
        })
        assert invalid.status_code == 422

        replaced = await client.put(url, json={
            "rating": "negative",
            "tags": ["lost_context"],
            "detail": None,
        })
        assert replaced.status_code == 200
        assert replaced.json()["rating"] == "negative"
        assert replaced.json()["tags"] == ["lost_context"]

        removed = await client.delete(url)
        assert removed.status_code == 204
        assert (await client.delete(url)).status_code == 204
        detail = await client.get(f"/api/v1/chat/{conv_id}")
        assert detail.json()["messages"][0]["feedback"] is None

    async def test_cross_user_message_is_hidden(
        self,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_admin: User,
    ):
        conv_id, msg_id = await _seed_message(db_manager, test_admin.id)
        response = await client.put(
            f"/api/v1/chat/{conv_id}/messages/{msg_id}/feedback",
            json={"rating": "positive", "tags": []},
        )
        assert response.status_code == 404


class TestAdminFeedbackBrowser:
    async def test_privacy_mode_redacts_feedback_owner(
        self,
        monkeypatch,
        admin_client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
    ):
        monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
        _, message_id = await _seed_message(db_manager, test_user.id)
        async with db_manager.session() as session:
            await MessageFeedbackRepository(session).upsert(
                message_id,
                rating="negative",
                tags=["incorrect_incomplete"],
                detail="needs review",
            )

        response = await admin_client.get("/api/v1/admin/feedback")

        assert response.status_code == 200
        (item,) = response.json()["feedback"]
        assert item["user_id"] is None
        assert item["user_display_name"] == "匿名用户"
        assert test_user.id not in response.text

    async def test_lists_filters_searches_and_exposes_message_feedback(
        self,
        admin_client: AsyncClient,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        positive_id = f"msg-positive-{uuid.uuid4().hex}"
        negative_id = f"msg-negative-{uuid.uuid4().hex}"
        async with db_manager.session() as session:
            conversation_repo = ConversationRepository(session)
            await conversation_repo.create_conversation(
                conv_id, title="Marked deployment review", user_id=test_user.id
            )
            await conversation_repo.add_message(conv_id, positive_id, "good turn")
            await conversation_repo.update_response(positive_id, "good response")
            await conversation_repo.add_message(
                conv_id, negative_id, "bad turn", parent_id=positive_id
            )
            await conversation_repo.update_response(negative_id, "bad response")

            feedback_repo = MessageFeedbackRepository(session)
            await feedback_repo.upsert(
                positive_id,
                rating="positive",
                tags=["high_quality"],
                detail=None,
            )
            await feedback_repo.upsert(
                negative_id,
                rating="negative",
                tags=["incorrect_incomplete"],
                detail="wrong result",
            )
            await session.execute(
                update(MessageFeedback)
                .where(MessageFeedback.message_id == positive_id)
                .values(updated_at=datetime(2026, 8, 6, 1, 0, 0))
            )
            await session.execute(
                update(MessageFeedback)
                .where(MessageFeedback.message_id == negative_id)
                .values(updated_at=datetime(2026, 8, 7, 1, 0, 0))
            )
            await session.commit()

        listing = await admin_client.get("/api/v1/admin/feedback")
        assert listing.status_code == 200
        assert [item["message_id"] for item in listing.json()["feedback"]] == [
            negative_id,
            positive_id,
        ]

        positive = await admin_client.get("/api/v1/admin/feedback?rating=positive")
        assert positive.status_code == 200
        assert [item["message_id"] for item in positive.json()["feedback"]] == [
            positive_id
        ]

        searched = await admin_client.get(f"/api/v1/admin/feedback?q={negative_id}")
        assert searched.status_code == 200
        assert searched.json()["feedback"][0]["feedback"]["detail"] == "wrong result"

        conversation = await admin_client.get(
            f"/api/v1/admin/conversations/{conv_id}/events"
        )
        assert conversation.status_code == 200
        groups = {item["message_id"]: item for item in conversation.json()["messages"]}
        assert groups[positive_id]["feedback"]["rating"] == "positive"
        assert groups[negative_id]["feedback"]["tags"] == ["incorrect_incomplete"]

        assert (await client.get("/api/v1/admin/feedback")).status_code == 403

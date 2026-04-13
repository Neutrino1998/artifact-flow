"""
Admin Router

Admin-only endpoints for observability and monitoring:
- GET /api/v1/admin/conversations — list all conversations with active status
- GET /api/v1/admin/conversations/{conv_id}/events — event timeline grouped by message
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    get_db_manager,
    get_runtime_store,
    require_admin,
)
from api.services.auth import TokenPayload
from api.schemas.admin import (
    AdminConversationSummary,
    AdminConversationListResponse,
    AdminEventItem,
    AdminMessageGroup,
    AdminConversationEventsResponse,
)
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


@router.get("/conversations", response_model=AdminConversationListResponse)
async def list_admin_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    user_id: Optional[str] = Query(default=None, max_length=64),
    _admin: TokenPayload = Depends(require_admin),
):
    """List all conversations (admin view) with active execution status."""
    from repositories.conversation_repo import ConversationRepository
    from db.models import User

    store = get_runtime_store()
    active_conv_ids = set(await store.list_active_conversations())

    db_manager = get_db_manager()
    async with db_manager.session() as session:
        repo = ConversationRepository(session)
        conversations = await repo.list_conversations(
            limit=limit,
            offset=offset,
            title_query=q.strip() if q else None,
            user_id=user_id,
            load_messages=True,
        )
        total = await repo.count_conversations(
            title_query=q.strip() if q else None,
            user_id=user_id,
        )

        # Batch-load user display names
        user_ids = {c.user_id for c in conversations if c.user_id}
        user_names: dict[str, str | None] = {}
        if user_ids:
            from sqlalchemy import select
            stmt = select(User.id, User.display_name, User.username).where(User.id.in_(user_ids))
            result = await session.execute(stmt)
            for uid, display_name, username in result.all():
                user_names[uid] = display_name or username

        items = [
            AdminConversationSummary(
                id=conv.id,
                title=conv.title,
                user_id=conv.user_id,
                user_display_name=user_names.get(conv.user_id) if conv.user_id else None,
                message_count=len(conv.messages) if conv.messages else 0,
                is_active=conv.id in active_conv_ids,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
            for conv in conversations
        ]

    return AdminConversationListResponse(
        conversations=items,
        total=total,
        has_more=offset + len(items) < total,
    )


@router.get(
    "/conversations/{conv_id}/events",
    response_model=AdminConversationEventsResponse,
)
async def get_admin_conversation_events(
    conv_id: str,
    _admin: TokenPayload = Depends(require_admin),
):
    """Get all events for a conversation, grouped by message."""
    from repositories.conversation_repo import ConversationRepository
    from repositories.message_event_repo import MessageEventRepository

    db_manager = get_db_manager()
    async with db_manager.session() as session:
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.get_conversation(conv_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages = await conv_repo.get_conversation_messages(conv_id)
        event_repo = MessageEventRepository(session)
        all_events = await event_repo.get_by_conversation(conv_id)

    # Group events by message_id
    events_by_msg: dict[str, list] = {}
    for e in all_events:
        events_by_msg.setdefault(e.message_id, []).append(e)

    groups = []
    for msg in messages:
        msg_events = events_by_msg.get(msg.id, [])

        # Extract execution_metrics from message metadata
        meta = msg.metadata_ or {}
        execution_metrics = meta.get("execution_metrics")

        groups.append(AdminMessageGroup(
            message_id=msg.id,
            user_input=msg.user_input,
            response=msg.response,
            created_at=msg.created_at,
            events=[
                AdminEventItem(
                    id=e.id,
                    event_type=e.event_type,
                    agent_name=e.agent_name,
                    data=e.data,
                    created_at=e.created_at,
                )
                for e in msg_events
            ],
            execution_metrics=execution_metrics,
        ))

    return AdminConversationEventsResponse(
        conversation_id=conv_id,
        title=conv.title,
        messages=groups,
    )

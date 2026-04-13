"""
Admin Router

Admin-only endpoints for observability and monitoring:
- GET /api/v1/admin/conversations — list all conversations with active status
- GET /api/v1/admin/conversations/{conv_id}/events — event timeline grouped by message
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    get_conversation_manager,
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
from core.conversation_manager import ConversationManager
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
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """List all conversations (admin view) with active execution status."""
    store = get_runtime_store()
    active_conv_ids = set(await store.list_active_conversations())

    conversations, total, user_names = await conversation_manager.list_admin_conversations(
        limit=limit,
        offset=offset,
        title_query=q.strip() if q else None,
        user_id=user_id,
    )

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
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Get all events for a conversation, grouped by message."""
    result = await conversation_manager.get_admin_conversation_events(conv_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv, messages, all_events = result

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

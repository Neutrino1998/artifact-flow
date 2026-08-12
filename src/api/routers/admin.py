"""
Admin Router

Admin-only endpoints for observability and monitoring:
- GET /api/v1/admin/feedback — list message-level user feedback
- GET /api/v1/admin/conversations — list all conversations with active status
- GET /api/v1/admin/conversations/{conv_id}/events — event timeline grouped by message
- GET /api/v1/admin/conversations/{conv_id}/stream — live active-execution events
- GET /api/v1/admin/conversations/{conv_id}/artifacts — flushed artifact list (no in-memory overlay)
- GET /api/v1/admin/conversations/{conv_id}/artifacts/{artifact_id} — current content + version list
- GET /api/v1/admin/conversations/{conv_id}/artifacts/{artifact_id}/raw — raw blob bytes
- GET /api/v1/admin/conversations/{conv_id}/artifacts/{artifact_id}/versions/{version} — specific version
"""

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.responses import StreamingResponse

from api.artifact_raw_response import RAW_ARTIFACT_RESPONSES, build_artifact_blob_response
from api.dependencies import (
    get_artifact_service,
    get_conversation_manager,
    get_runtime_status_reader,
    get_stream_transport,
    require_admin,
)
from api.services.auth import TokenPayload
from api.services.runtime_status_reader import RuntimeStatusReader
from api.services.stream_transport import StreamTransport
from api.routers.stream import SSE_OPENAPI_RESPONSES, build_stream_response
from api.schemas.admin import (
    AdminConversationSummary,
    AdminConversationListResponse,
    AdminFeedbackItem,
    AdminFeedbackListResponse,
    AdminEventItem,
    AdminMessageGroup,
    AdminConversationEventsResponse,
    AdminPromptReconstructResponse,
)
from api.schemas.artifact import (
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactSummary,
    VersionDetailResponse,
    VersionSummary,
)
from core.conversation_manager import ConversationManager
from tools.builtin.artifact_service import ArtifactService
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


@router.get("/feedback", response_model=AdminFeedbackListResponse)
async def list_admin_feedback(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    rating: Optional[Literal["positive", "negative"]] = Query(default=None),
    _admin: TokenPayload = Depends(require_admin),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """List message-level feedback newest-first; admin access is read-only."""
    rows, total, user_names = await conversation_manager.list_admin_feedback(
        rating=rating,
        query=q.strip() if q else None,
        limit=limit,
        offset=offset,
    )
    items = [
        AdminFeedbackItem(
            conversation_id=conv.id,
            conversation_title=conv.title,
            user_id=conv.user_id,
            user_display_name=user_names.get(conv.user_id) if conv.user_id else None,
            message_id=message.id,
            user_input=message.user_input,
            feedback=feedback,
        )
        for feedback, message, conv in rows
    ]
    return AdminFeedbackListResponse(
        feedback=items,
        total=total,
        has_more=offset + len(items) < total,
    )


@router.get("/conversations", response_model=AdminConversationListResponse)
async def list_admin_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    user_id: Optional[str] = Query(default=None, max_length=64),
    _admin: TokenPayload = Depends(require_admin),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runtime_status: RuntimeStatusReader = Depends(get_runtime_status_reader),
):
    """List all conversations (admin view) with active execution status."""
    active_executions = await runtime_status.list_active_executions()

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
            is_active=conv.id in active_executions,
            active_message_id=active_executions.get(conv.id),
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
    runtime_status: RuntimeStatusReader = Depends(get_runtime_status_reader),
):
    """Get all events for a conversation, grouped by message."""
    result = await conversation_manager.get_admin_conversation_events(conv_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv, messages, all_events, owner_display_name = result

    active_message_id = await runtime_status.get_active_message_id(conv_id)

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
            parent_id=msg.parent_id,
            user_input=msg.user_input,
            response=msg.response,
            created_at=msg.created_at,
            events=[
                AdminEventItem(
                    id=e.id,
                    event_id=e.event_id,
                    event_type=e.event_type,
                    agent_name=e.agent_name,
                    data=e.data,
                    created_at=e.created_at,
                )
                for e in msg_events
            ],
            execution_metrics=execution_metrics,
            feedback=msg.feedback,
            uploaded_files=meta.get("uploaded_files"),
        ))

    return AdminConversationEventsResponse(
        conversation_id=conv_id,
        title=conv.title,
        user_id=conv.user_id,
        user_display_name=owner_display_name,
        active_branch=conv.active_branch,
        is_active=active_message_id is not None,
        active_message_id=active_message_id,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=groups,
    )


@router.get(
    "/conversations/{conv_id}/stream",
    response_class=StreamingResponse,
    responses=SSE_OPENAPI_RESPONSES,
)
async def stream_admin_conversation(
    conv_id: str,
    request: Request,
    _admin: TokenPayload = Depends(require_admin),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_transport: StreamTransport = Depends(get_stream_transport),
    runtime_status: RuntimeStatusReader = Depends(get_runtime_status_reader),
) -> StreamingResponse:
    """Observe the active execution for one conversation (admin read-only)."""
    conv = await conversation_manager.get_conversation_detail(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    message_id = await runtime_status.get_active_stream_message_id(conv_id)
    if message_id is None:
        raise HTTPException(status_code=404, detail="No active stream for conversation")

    # Reuse the owner's stream authorization after the admin-only conversation
    # lookup.  This keeps owner validation enabled inside both transports instead
    # of introducing an accidental user_id=None bypass.
    return build_stream_response(
        stream_id=message_id,
        stream_transport=stream_transport,
        user_id=conv.user_id,
        last_event_id=request.headers.get("Last-Event-ID"),
        event_view="admin",
    )


@router.get(
    "/conversations/{conv_id}/messages/{message_id}/reconstruct",
    response_model=AdminPromptReconstructResponse,
)
async def reconstruct_admin_prompt(
    conv_id: str,
    message_id: str,
    agent_start_event_id: str = Query(..., max_length=96),
    _admin: TokenPayload = Depends(require_admin),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """重建某一发 LLM 调用的 messages 语义输入（admin 取证，按 agent_start 锚定）。

    锚 = 该次调用前发出的 agent_start 事件（其 event_id 由 events 端点返回）。重建走
    分支正确的 path，复用引擎同一套 messages 装配逻辑，并返回当次持久化的 model；
    不重新生成动态内容，也不声称还原未持久化的 native tools schema —— 详见
    ConversationManager.reconstruct_prompt。
    """
    result = await conversation_manager.reconstruct_prompt(
        conv_id, message_id, agent_start_event_id
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation, message, or agent_start event not found on this branch path",
        )
    return AdminPromptReconstructResponse(**result)


# ============================================================
# Artifacts (admin view)
#
# Reads DB-only flushed state. After the artifact-layer refactor (removal of
# the _active_managers process registry) ALL REST reads are DB-only — there is
# no in-memory overlay anywhere. Matches the rest of admin observability, which
# views persisted history rather than live execution state. No ownership check
# (admin sees all users' conversations); `require_admin` still gates access.
# ============================================================


@router.get(
    "/conversations/{conv_id}/artifacts",
    response_model=ArtifactListResponse,
)
async def list_admin_conversation_artifacts(
    conv_id: str,
    _admin: TokenPayload = Depends(require_admin),
    artifact_service: ArtifactService = Depends(get_artifact_service),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """List all artifacts in a conversation (DB-only, no in-memory overlay)."""
    # Match events endpoint: 404 when conv doesn't exist so admin UI can tell
    # "no artifacts" apart from "no such conversation".
    if not await conversation_manager.exists_async(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    artifacts = await artifact_service.list_artifacts(
        session_id=conv_id,
        include_content=False,
    )
    return ArtifactListResponse(
        session_id=conv_id,
        artifacts=[
            ArtifactSummary(
                id=art["id"],
                content_type=art["content_type"],
                title=art["title"],
                current_version=art["version"],
                source=art.get("source"),
                original_filename=art.get("original_filename"),
                has_blob=bool(art.get("has_blob")),
                created_at=datetime.fromisoformat(art["created_at"]),
                updated_at=datetime.fromisoformat(art["updated_at"]),
            )
            for art in artifacts
        ],
    )


@router.get(
    "/conversations/{conv_id}/artifacts/{artifact_id}",
    response_model=ArtifactResponse,
)
async def get_admin_conversation_artifact(
    conv_id: str,
    artifact_id: str,
    _admin: TokenPayload = Depends(require_admin),
    artifact_service: ArtifactService = Depends(get_artifact_service),
):
    """Get current artifact content + version list (DB-only)."""
    result = await artifact_service.read_artifact(
        session_id=conv_id,
        artifact_id=artifact_id,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found in conversation '{conv_id}'",
        )

    versions = await artifact_service.list_versions(conv_id, artifact_id)
    version_summaries = [
        VersionSummary(
            version=v.version,
            update_type=v.update_type,
            created_at=v.created_at,
        )
        for v in versions
    ]

    return ArtifactResponse(
        id=result["id"],
        session_id=conv_id,
        content_type=result["content_type"],
        title=result["title"],
        content=result["content"],
        current_version=result["version"],
        source=result.get("source"),
        original_filename=result.get("original_filename"),
        has_blob=bool(result.get("has_blob")),
        created_at=datetime.fromisoformat(result["created_at"]),
        updated_at=datetime.fromisoformat(result["updated_at"]),
        versions=version_summaries,
    )


@router.get(
    "/conversations/{conv_id}/artifacts/{artifact_id}/raw",
    response_class=Response,
    responses=RAW_ARTIFACT_RESPONSES,
)
async def get_admin_conversation_artifact_raw(
    conv_id: str,
    artifact_id: str,
    _admin: TokenPayload = Depends(require_admin),
    artifact_service: ArtifactService = Depends(get_artifact_service),
):
    """Serve raw blob bytes for an artifact in any conversation (admin-only)."""
    blob = await artifact_service.get_blob(conv_id, artifact_id)
    if blob is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact blob '{artifact_id}' not found in conversation '{conv_id}'",
        )
    return build_artifact_blob_response(blob)


@router.get(
    "/conversations/{conv_id}/artifacts/{artifact_id}/versions/{version}",
    response_model=VersionDetailResponse,
)
async def get_admin_conversation_artifact_version(
    conv_id: str,
    artifact_id: str,
    version: int,
    _admin: TokenPayload = Depends(require_admin),
    artifact_service: ArtifactService = Depends(get_artifact_service),
):
    """Get a specific historical version's content (DB-only)."""
    ver = await artifact_service.get_version(conv_id, artifact_id, version)
    if ver is None:
        raise HTTPException(
            status_code=404,
            detail=f"Version {version} of artifact '{artifact_id}' not found",
        )
    return VersionDetailResponse(
        version=ver.version,
        content=ver.content,
        update_type=ver.update_type,
        created_at=ver.created_at,
    )

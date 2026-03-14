"""
Chat Router

处理对话相关的 API 端点：
- POST /api/v1/chat - 发送消息
- GET /api/v1/chat - 列出对话
- GET /api/v1/chat/{conv_id} - 获取对话详情
- DELETE /api/v1/chat/{conv_id} - 删除对话
- POST /api/v1/chat/{conv_id}/resume - 恢复中断执行
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, AsyncIterator, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from api.config import config
from api.dependencies import (
    get_agents,
    get_conversation_manager,
    get_current_user,
    get_db_manager,
    get_db_session,
    get_stream_manager,
    get_task_manager,
    get_tools,
)
from api.services.auth import TokenPayload
from api.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ResumeRequest,
    ResumeResponse,
    ConversationListResponse,
    ConversationDetailResponse,
    ConversationSummary,
    MessageResponse,
)
from api.services.stream_manager import StreamAlreadyExistsError, StreamManager
from api.services.task_manager import TaskManager, DuplicateExecutionError
from core.conversation_manager import ConversationManager
from repositories.base import NotFoundError
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger, set_request_context

logger = get_logger("ArtifactFlow")

router = APIRouter()


async def _verify_ownership(
    conv_id: str, user: TokenPayload, repo: ConversationRepository
) -> None:
    """校验 conversation 归属当前用户，不匹配返回 404"""
    conv = await repo.get_conversation(conv_id)
    if not conv or conv.user_id != user.user_id:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


def _sanitize_error_event(event: dict) -> dict:
    """Strip internal error details from error events in production."""
    if config.DEBUG:
        return event
    if event.get("type") == "error" and isinstance(event.get("data"), dict):
        event = {**event, "data": {**event["data"], "error": "Internal server error"}}
    return event


@asynccontextmanager
async def _create_controller() -> AsyncGenerator:
    """
    Build a fresh ExecutionController with its own DB session.

    Why not use Depends(get_controller)?
    send_message() launches a background task whose lifetime exceeds the HTTP request.
    Depends(get_db_session) closes the session when the request ends, but the background
    task still needs a live session. This context manager provides an independent session
    scoped to the background task's lifetime.

    Usage:
        async with _create_controller() as ctrl:
            async for event in ctrl.stream_execute(...):
                ...
    """
    from core.controller import ExecutionController
    from core.conversation_manager import ConversationManager as CM
    from tools.implementations.artifact_ops import ArtifactManager, create_artifact_tools
    from repositories.artifact_repo import ArtifactRepository
    from repositories.conversation_repo import ConversationRepository as CR
    from repositories.message_event_repo import MessageEventRepository

    db_manager = get_db_manager()
    task_manager = get_task_manager()
    agents = get_agents()

    async with db_manager.session() as session:
        artifact_repo = ArtifactRepository(session)
        artifact_manager = ArtifactManager(artifact_repo)

        # 合并全局工具 + 请求级 artifact 工具
        artifact_tools = create_artifact_tools(artifact_manager)
        all_tools = {**get_tools(), **{t.name: t for t in artifact_tools}}

        conv_repo = CR(session)
        conv_manager = CM(conv_repo)
        event_repo = MessageEventRepository(session)

        yield ExecutionController(
            agents=agents,
            tools=all_tools,
            task_manager=task_manager,
            artifact_manager=artifact_manager,
            conversation_manager=conv_manager,
            message_event_repo=event_repo,
            permission_timeout=config.PERMISSION_TIMEOUT,
        )


async def _run_and_push(
    stream_manager: StreamManager,
    stream_id: str,
    event_stream: AsyncIterator[dict],
) -> None:
    """
    Consume events from a controller stream and push them to the StreamManager.

    Handles timeout and unexpected errors, pushing sanitized error events.
    Execution runs to completion even if the SSE client disconnects.
    """
    stream_closed = False
    try:
        async with asyncio.timeout(config.STREAM_TIMEOUT):
            async for event in event_stream:
                if stream_closed:
                    continue
                if not await stream_manager.push_event(stream_id, _sanitize_error_event(event)):
                    logger.info(f"Stream {stream_id} closed, execution will continue to completion")
                    stream_closed = True

    except TimeoutError:
        logger.error(f"Execution timed out after {config.STREAM_TIMEOUT}s for {stream_id}")
        await stream_manager.push_event(stream_id, _sanitize_error_event({
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "data": {"success": False, "error": f"Execution timed out after {config.STREAM_TIMEOUT}s"}
        }))

    except Exception as e:
        logger.exception(f"Error in execution: {e}")
        await stream_manager.push_event(stream_id, _sanitize_error_event({
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "data": {"success": False, "error": str(e)}
        }))


@router.post("", response_model=ChatResponse)
async def send_message(
    request: ChatRequest,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_manager: StreamManager = Depends(get_stream_manager),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    发送新消息

    启动执行，返回 stream_url 供前端订阅。
    """
    from uuid import uuid4

    # 为新消息准备 ID
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = f"conv-{uuid4().hex}"

    message_id = f"msg-{uuid4().hex}"
    user_id = current_user.user_id

    # 已有会话：校验归属
    if request.conversation_id:
        repo = conversation_manager._ensure_repository()
        await _verify_ownership(conversation_id, current_user, repo)

    # 确保 conversation 存在
    await conversation_manager.ensure_conversation_exists(conversation_id, user_id=user_id)

    # 创建 stream（使用 message_id 作为 stream key）
    await stream_manager.create_stream(message_id, owner_user_id=user_id)

    # 设置请求上下文
    set_request_context(message_id=message_id, conv_id=conversation_id)

    # 启动后台任务
    async def execute_and_push():
        try:
            async with _create_controller() as ctrl:
                parent_kwargs = {}
                if 'parent_message_id' in request.model_fields_set:
                    parent_kwargs['parent_message_id'] = request.parent_message_id
                await _run_and_push(
                    stream_manager,
                    message_id,
                    ctrl.stream_execute(
                        content=request.content,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        **parent_kwargs,
                    ),
                )
        except Exception as e:
            logger.exception(f"Failed to initialize execution: {e}")
            await stream_manager.push_event(message_id, _sanitize_error_event({
                "type": "error",
                "timestamp": datetime.now().isoformat(),
                "data": {"success": False, "error": str(e)}
            }))

    await task_manager.submit(message_id, execute_and_push())

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        stream_url=f"/api/v1/stream/{message_id}"
    )


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """列出对话列表"""
    user_id = current_user.user_id
    total = await conversation_manager.count_conversations_async(user_id=user_id)
    conversations = await conversation_manager.list_conversations_async(
        limit=limit, offset=offset, user_id=user_id
    )

    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                id=conv["conversation_id"],
                title=conv.get("title"),
                message_count=conv.get("message_count", 0),
                created_at=datetime.fromisoformat(conv["created_at"]) if isinstance(conv["created_at"], str) else conv["created_at"],
                updated_at=datetime.fromisoformat(conv["updated_at"]) if isinstance(conv["updated_at"], str) else conv["updated_at"],
            )
            for conv in conversations
        ],
        total=total,
        has_more=offset + len(conversations) < total,
    )


@router.get("/{conv_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """获取对话详情（含消息树）"""
    try:
        repo = conversation_manager._ensure_repository()
        await _verify_ownership(conv_id, current_user, repo)

        conversation = await repo.get_conversation(conv_id, load_messages=True)
        if not conversation:
            raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")

        messages = await repo.get_conversation_messages(conv_id)

        children_map = {}
        for msg in messages:
            if msg.parent_id:
                if msg.parent_id not in children_map:
                    children_map[msg.parent_id] = []
                children_map[msg.parent_id].append(msg.id)

        return ConversationDetailResponse(
            id=conv_id,
            title=conversation.title,
            active_branch=conversation.active_branch,
            messages=[
                MessageResponse(
                    id=msg.id,
                    parent_id=msg.parent_id,
                    content=msg.content,
                    response=msg.response,
                    created_at=msg.created_at,
                    children=children_map.get(msg.id, []),
                )
                for msg in messages
            ],
            session_id=conv_id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """删除对话"""
    try:
        repo = conversation_manager._ensure_repository()
        await _verify_ownership(conv_id, current_user, repo)

        success = await repo.delete_conversation(conv_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")

        conversation_manager.clear_cache(conv_id)
        return {"success": True, "message": f"Conversation '{conv_id}' deleted"}

    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


@router.get("/{conv_id}/messages/{msg_id}/events")
async def get_message_events(
    conv_id: str,
    msg_id: str,
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """查询消息的事件链（用于历史回放和可观测性）"""
    from repositories.message_event_repo import MessageEventRepository

    repo = conversation_manager._ensure_repository()
    await _verify_ownership(conv_id, current_user, repo)

    # 校验 message 归属
    message = await repo.get_message(msg_id)
    if not message or message.conversation_id != conv_id:
        raise HTTPException(status_code=404, detail="Message not found")

    db_manager = get_db_manager()
    async with db_manager.session() as session:
        event_repo = MessageEventRepository(session)
        if event_type:
            events = await event_repo.get_by_type(msg_id, event_type)
        else:
            events = await event_repo.get_by_message(msg_id)

    return {
        "message_id": msg_id,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "agent_name": e.agent_name,
                "data": e.data,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "total": len(events),
    }


@router.post("/{conv_id}/resume", response_model=ResumeResponse)
async def resume_execution(
    conv_id: str,
    request: ResumeRequest,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_manager: StreamManager = Depends(get_stream_manager),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    恢复中断的执行（权限确认后）

    通过 TaskManager.resolve_interrupt() 唤醒暂停的 coroutine。
    """
    message_id = request.message_id

    # 校验 conversation 归属
    repo = conversation_manager._ensure_repository()
    await _verify_ownership(conv_id, current_user, repo)

    # 校验 message 归属
    message = await repo.get_message(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.conversation_id != conv_id:
        raise HTTPException(status_code=403, detail="Message does not belong to this conversation")

    # 解决 interrupt（唤醒 coroutine）
    resume_data = {
        "approved": request.approved,
        "always_allow": request.always_allow,
    }

    result = await task_manager.resolve_interrupt(message_id, resume_data)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="No pending interrupt found for this message")
    if result == "already_resolved":
        raise HTTPException(status_code=409, detail="Interrupt already resolved for this message")

    # 不需要创建新 stream — 原来的 coroutine 继续执行，
    # 事件会继续推送到原来的 stream（使用 message_id 作为 stream key）
    return ResumeResponse(
        stream_url=f"/api/v1/stream/{message_id}"
    )

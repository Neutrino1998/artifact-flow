"""
Chat Router

处理对话相关的 API 端点：
- POST /api/v1/chat - 发送消息
- POST /api/v1/chat/{conv_id}/inject - 向活跃执行注入消息
- GET /api/v1/chat - 列出对话
- GET /api/v1/chat/{conv_id} - 获取对话详情
- DELETE /api/v1/chat/{conv_id} - 删除对话
- POST /api/v1/chat/{conv_id}/resume - 恢复中断执行
"""

from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    get_compaction_manager,
    get_conversation_manager,
    get_current_user,
    get_db_manager,
    get_db_session,
    get_stream_transport,
    get_execution_runner,
)
from api.services.auth import TokenPayload
from api.schemas.chat import (
    CancelResponse,
    ChatRequest,
    ChatResponse,
    InjectRequest,
    InjectResponse,
    ResumeRequest,
    ResumeResponse,
    ConversationListResponse,
    ConversationDetailResponse,
    ConversationSummary,
    MessageResponse,
)
from api.services.controller_factory import create_controller, run_and_push, sanitize_error_event
from api.services.stream_transport import StreamTransport
from api.services.execution_runner import ConflictError, ExecutionRunner
from core.conversation_manager import ConversationManager
from repositories.base import NotFoundError
from utils.logger import get_logger, set_request_context

logger = get_logger("ArtifactFlow")

router = APIRouter()


async def _verify_ownership(
    conv_id: str, user: TokenPayload, conversation_manager: ConversationManager
) -> None:
    """校验 conversation 归属当前用户，不匹配返回 404"""
    if not await conversation_manager.verify_ownership(conv_id, user.user_id):
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


@router.post("", response_model=ChatResponse)
async def send_message(
    request: ChatRequest,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_transport: StreamTransport = Depends(get_stream_transport),
    runner: ExecutionRunner = Depends(get_execution_runner),
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
        await _verify_ownership(conversation_id, current_user, conversation_manager)

    # 确保 conversation 存在（失败需返回 HTTP 错误，保留在路由层）
    await conversation_manager.ensure_conversation_exists(conversation_id, user_id=user_id)

    # 设置请求上下文
    set_request_context(message_id=message_id, conv_id=conversation_id)

    # 构造执行闭包
    async def execute_and_push():
        try:
            async with create_controller(conversation_id, message_id) as ctrl:
                parent_kwargs = {}
                if 'parent_message_id' in request.model_fields_set:
                    parent_kwargs['parent_message_id'] = request.parent_message_id
                await run_and_push(
                    stream_transport,
                    message_id,
                    ctrl.stream_execute(
                        user_input=request.user_input,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        **parent_kwargs,
                    ),
                )
        except Exception as e:
            logger.exception(f"Failed to initialize execution: {e}")
            await stream_transport.push_event(message_id, sanitize_error_event({
                "type": "error",
                "timestamp": datetime.now().isoformat(),
                "data": {"success": False, "error": str(e)}
            }))

    # submit 内部处理 lease + interactive + stream 编排
    try:
        await runner.submit(
            conversation_id, message_id, execute_and_push,
            user_id=user_id, stream_transport=stream_transport,
        )
    except ConflictError:
        raise HTTPException(
            status_code=409,
            detail="An execution is already active for this conversation. "
                   "Use POST /chat/{conv_id}/inject to send input to the running execution.",
        )

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        stream_url=f"/api/v1/stream/{message_id}"
    )


@router.get("/{conv_id}/active-stream")
async def get_active_stream(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_transport: StreamTransport = Depends(get_stream_transport),
    runner: ExecutionRunner = Depends(get_execution_runner),
):
    """查询会话是否有活跃的执行流，用于断线重连"""
    await _verify_ownership(conv_id, current_user, conversation_manager)

    message_id = await runner.store.get_leased_message_id(conv_id)
    if not message_id:
        raise HTTPException(status_code=404, detail="No active execution")

    # 校验 stream 是否仍存活（meta key 未过期）
    if not await stream_transport.is_stream_alive(message_id):
        raise HTTPException(status_code=410, detail="Stream expired")

    return {
        "conversation_id": conv_id,
        "message_id": message_id,
        "stream_url": f"/api/v1/stream/{message_id}",
    }


@router.post("/{conv_id}/inject", response_model=InjectResponse)
async def inject_message(
    conv_id: str,
    request: InjectRequest,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runner: ExecutionRunner = Depends(get_execution_runner),
):
    """
    向活跃执行注入消息

    仅当 conversation 有正在运行的执行时可用。
    注入的消息通过 queued_message 事件进入 lead agent 的 context。
    前端不应重建 SSE 连接 — 事件仍通过原有 stream 推送。

    注入内容通过 queued_message 事件持久化到 MessageEvent 表，
    可通过 GET /chat/{conv_id}/messages/{msg_id}/events 查询。
    不创建独立 Message 记录（注入是同一轮执行的补充输入，非独立对话轮次）。
    """
    await _verify_ownership(conv_id, current_user, conversation_manager)

    active_msg_id = await runner.store.get_interactive_message_id(conv_id)
    if not active_msg_id:
        raise HTTPException(status_code=409, detail="No active execution for this conversation")

    await runner.store.inject_message(active_msg_id, request.content)

    return InjectResponse(
        message_id=active_msg_id,
        stream_url=f"/api/v1/stream/{active_msg_id}",
    )


@router.post("/{conv_id}/cancel", response_model=CancelResponse)
async def cancel_execution(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runner: ExecutionRunner = Depends(get_execution_runner),
):
    """
    取消活跃执行

    请求取消 conversation 当前正在运行的执行。引擎会在下一个检查点优雅退出。
    """
    await _verify_ownership(conv_id, current_user, conversation_manager)

    active_msg_id = await runner.store.get_interactive_message_id(conv_id)
    if not active_msg_id:
        raise HTTPException(status_code=409, detail="No active execution for this conversation")

    await runner.store.request_cancel(active_msg_id)

    return CancelResponse(message_id=active_msg_id)


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
                created_at=datetime.fromisoformat(conv["created_at"]),
                updated_at=datetime.fromisoformat(conv["updated_at"]),
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
        await _verify_ownership(conv_id, current_user, conversation_manager)

        conversation = await conversation_manager.get_conversation_detail(conv_id)
        if not conversation:
            raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")

        messages = await conversation_manager.get_conversation_messages(conv_id)

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
                    user_input=msg.user_input,
                    response=msg.response,
                    user_input_summary=msg.user_input_summary,
                    response_summary=msg.response_summary,
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
        await _verify_ownership(conv_id, current_user, conversation_manager)

        success = await conversation_manager.delete_conversation(conv_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")

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

    await _verify_ownership(conv_id, current_user, conversation_manager)

    # 校验 message 归属
    message = await conversation_manager.get_message(msg_id)
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
    stream_transport: StreamTransport = Depends(get_stream_transport),
    runner: ExecutionRunner = Depends(get_execution_runner),
):
    """
    恢复中断的执行（权限确认后）

    通过 RuntimeStore.resolve_interrupt() 唤醒暂停的 coroutine。
    """
    message_id = request.message_id

    # 校验 conversation 归属
    await _verify_ownership(conv_id, current_user, conversation_manager)

    # 校验 message 归属
    message = await conversation_manager.get_message(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.conversation_id != conv_id:
        raise HTTPException(status_code=404, detail="Message not found")

    # 解决 interrupt（唤醒 coroutine）
    resume_data = {
        "approved": request.approved,
        "always_allow": request.always_allow,
    }

    result = await runner.store.resolve_interrupt(message_id, resume_data)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="No pending interrupt found for this message")
    if result == "already_resolved":
        raise HTTPException(status_code=409, detail="Interrupt already resolved for this message")

    # 不需要创建新 stream — 原来的 coroutine 继续执行，
    # 事件会继续推送到原来的 stream（使用 message_id 作为 stream key）
    return ResumeResponse(
        stream_url=f"/api/v1/stream/{message_id}"
    )


@router.post("/{conv_id}/compact")
async def trigger_compaction(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """手动触发对话 compaction"""
    await _verify_ownership(conv_id, current_user, conversation_manager)

    compaction_manager = get_compaction_manager()
    if compaction_manager is None:
        raise HTTPException(status_code=503, detail="Compaction service not available")

    started = await compaction_manager.trigger(conv_id)
    if not started:
        raise HTTPException(status_code=409, detail="Compaction already in progress for this conversation")

    return {"status": "accepted", "conversation_id": conv_id}

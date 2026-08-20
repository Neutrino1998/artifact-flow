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

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError

from config import config
from api.event_projection import project_event_data_for_user
from api.dependencies import (
    get_conversation_manager,
    get_current_user,
    get_conversation_execution_service,
    get_runtime_status_reader,
)
from api.services.auth import TokenPayload
from api.schemas.chat import (
    BulkDeleteFailedItem,
    BulkDeleteRequest,
    BulkDeleteResponse,
    CancelResponse,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    ActiveStreamResponse,
    InjectRequest,
    InjectResponse,
    ResumeRequest,
    ResumeResponse,
    ConversationListResponse,
    ConversationDetailResponse,
    ConversationSummary,
    MessageResponse,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    StorageUsageResponse,
)
from api.services.conversation_execution_service import (
    AUTO_PARENT,
    ConversationAdmissionUnavailable,
    ConversationExecutionConflict,
    ConversationExecutionService,
    ConversationTurnRequest,
    ExecutionStillQueued,
    InvalidParentMessage,
    NoActiveExecution,
    PendingInterruptAlreadyResolved,
    PendingInterruptNotFound,
    PendingInterruptStale,
    ReferencedArtifactNotFound,
    UploadQuotaExceeded,
)
from api.services.runtime_status_reader import RuntimeStatusReader
from api.services.runtime_store import InjectQueueFull
from api.services.upload_conversion import convert_uploaded_file
from core.management.conversation_manager import (
    ConversationManager,
    ConversationResourceNotFoundError,
)
from utils.logger import get_logger

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
    payload: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    current_user: TokenPayload = Depends(get_current_user),
    execution_service: ConversationExecutionService = Depends(
        get_conversation_execution_service
    ),
):
    """
    发送新消息（multipart/form-data）

    `payload` 为 ChatRequest 的 JSON 字符串；`files` 为可选附件。附件在起 turn
    前同步转成 artifact（source=user_upload）落库，并把 (id, filename) 透传进
    USER_INPUT 事件正文，让 agent 知道哪些是本轮新传的。返回 stream_url 供前端订阅。
    """
    # 解析 + 校验 JSON payload：model_validate_json 保留 model_fields_set，
    # 故 parent_message_id 的 omit/null/id 三态语义不变；超 max_length 等失败 → 422
    try:
        request = ChatRequest.model_validate_json(payload)
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise HTTPException(status_code=422, detail=f"Invalid chat payload: {msgs}")

    # 附件数量上限：尽早拒绝（在建会话 / 转换之前），避免无界附件导致长时间串行
    # 转换 + DB 写入 + USER_INPUT 归属串膨胀。每个文件的大小限制（MAX_UPLOAD_SIZE）
    # 仍在转换处生效；批量总字节由代理层独立封顶。
    attachment_count = sum(1 for f in files if f.filename)
    if attachment_count > config.MAX_CHAT_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many attachments: {attachment_count} (max {config.MAX_CHAT_ATTACHMENTS})",
        )

    # 空白正文且无附件 = 本轮无可处理输入：USER_INPUT 正文为空 → 被 EventHistory 过滤
    # → history 为空 → build() 在 [-1] 崩。边界即拒（前端 sendDisabled 同条件，这里是
    # 非 UI 客户端的兜底）；带附件时由归属串补足正文，故仅无附件时要求非空。
    # force_compact / activate_skills / referenced_artifact_ids 与附件同理：execute_loop
    # 会向 USER_INPUT 正文注入相应的非空说明，故仅选择这些结构化输入也可发送。
    if (
        not request.user_input.strip()
        and attachment_count == 0
        and not request.force_compact
        and not request.activate_skills
        and not request.referenced_artifact_ids
    ):
        raise HTTPException(
            status_code=422,
            detail="user_input must not be blank when no files are attached",
        )

    user_id = current_user.user_id

    # 附件:相一 **纯转换**（bytes → 文本），不碰 DB、不 commit 任何 artifact。任一附件
    # 格式不支持 / 无法解码 / 转换失败 → 在此抛 422/500，此时 conversation 与 artifact 都
    # 未创建，批次「全有或全无」。转换后的内容 closure-carry 进控制器，由 execute_loop 在
    # turn 起点 stage 进 WorkingSet（统一生命周期：发 ARTIFACT_CREATED、随 turn 末 flush
    # 落库）——**不在此即时 commit**。
    # 由此「上传即时 commit」退场带来的两个好处:
    #   - `_N` 去重副本 bug 消失:submit 抛 409（已有活跃执行）时尚未 stage 任何东西，
    #     execute_and_push 根本不会跑 → 重发不产生副本（旧实现在 submit 前已 commit）。
    #   - 上传与模型产物的「turn 中途死即丢失」语义一致（皆 ephemeral，随 lease 重启而失）；
    #     turn 中途死则上传丢失，用户从本地重新选文件重试（composer 发送即清空，不做保留）。
    converted = [
        await convert_uploaded_file(f)
        for f in files
        if f.filename  # 空 file part（前端无附件时不应出现，防御性跳过）
    ]

    # 转换后内容打包 closure-carry 给控制器（不 commit）。
    uploaded_files: List[dict] = [
        {
            "filename": c.filename,
            "content": c.content,
            "content_type": c.content_type,
            "metadata": c.metadata,
            "blob": c.blob,                          # 二进制源(图片/富格式),纯文本为 None
        }
        for c in converted
    ]

    parent_message_id = (
        request.parent_message_id
        if "parent_message_id" in request.model_fields_set
        else AUTO_PARENT
    )
    try:
        handle = await execution_service.submit_turn(ConversationTurnRequest(
            user_id=user_id,
            user_input=request.user_input,
            conversation_id=request.conversation_id,
            parent_message_id=parent_message_id,
            uploaded_files=uploaded_files,
            force_compact=request.force_compact,
            activate_skills=request.activate_skills,
            referenced_artifact_ids=request.referenced_artifact_ids,
        ))
    except ConversationResourceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Conversation '{request.conversation_id}' not found",
        )
    except ReferencedArtifactNotFound:
        raise HTTPException(
            status_code=404,
            detail="Referenced file not found in this conversation",
        )
    except InvalidParentMessage:
        logger.warning(
            "Chat rejected (422): invalid parent_message_id for conversation "
            f"(conv={request.conversation_id}, parent={request.parent_message_id})"
        )
        raise HTTPException(
            status_code=422,
            detail="parent_message_id does not belong to this conversation",
        )
    except UploadQuotaExceeded as exc:
        quota_mb = exc.quota_bytes / 1024 / 1024
        raise HTTPException(
            status_code=413,
            detail=(
                f"存储空间不足：本次上传（{exc.incoming_bytes / 1024 / 1024:.1f}MB）"
                f"将超出你的 {quota_mb:.0f}MB 存储配额"
                f"（当前已用 {exc.used_bytes / 1024 / 1024:.1f}MB）。"
                "请删除一些对话或已导入的技能以释放空间后重试。"
            ),
        )
    except ConversationExecutionConflict:
        raise HTTPException(
            status_code=409,
            detail="An execution is already active for this conversation. "
                   "Use POST /chat/{conv_id}/inject to send input to the running execution.",
        )
    except ConversationAdmissionUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Execution admission could not confirm ownership; please retry.",
        )

    return ChatResponse(
        conversation_id=handle.conversation_id,
        message_id=handle.message_id,
        stream_url=handle.stream_url,
    )


@router.get("/{conv_id}/active-stream", response_model=ActiveStreamResponse)
async def get_active_stream(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runtime_status: RuntimeStatusReader = Depends(get_runtime_status_reader),
):
    """查询会话是否有活跃的执行流，用于断线重连。无活跃流是正常空状态。"""
    await _verify_ownership(conv_id, current_user, conversation_manager)

    message_id = await runtime_status.get_active_stream_message_id(conv_id)
    if not message_id:
        return ActiveStreamResponse(active=False, conversation_id=conv_id)

    return ActiveStreamResponse(
        active=True,
        conversation_id=conv_id,
        message_id=message_id,
        stream_url=f"/api/v1/stream/{message_id}",
    )


@router.post("/{conv_id}/inject", response_model=InjectResponse)
async def inject_message(
    conv_id: str,
    request: InjectRequest,
    current_user: TokenPayload = Depends(get_current_user),
    execution_service: ConversationExecutionService = Depends(
        get_conversation_execution_service
    ),
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
    try:
        active_msg_id = await execution_service.inject(
            conv_id, current_user.user_id, request.content
        )
    except ConversationResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")
    except NoActiveExecution:
        raise HTTPException(status_code=409, detail="No active execution for this conversation")
    except InjectQueueFull:
        # Transient backpressure: the queue drains every LLM round, so the
        # client can retry shortly. The running turn is unaffected.
        raise HTTPException(
            status_code=429,
            detail="Too many pending messages; the agent is still consuming the queue, retry shortly.",
        )

    return InjectResponse(
        message_id=active_msg_id,
        stream_url=f"/api/v1/stream/{active_msg_id}",
    )


@router.post("/{conv_id}/cancel", response_model=CancelResponse)
async def cancel_execution(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    execution_service: ConversationExecutionService = Depends(
        get_conversation_execution_service
    ),
):
    """
    取消活跃执行

    请求取消 conversation 当前正在运行的执行。引擎会在下一个检查点优雅退出。
    """
    # cancel gate 在 interactive（== RUNNING），与 inject 对称：只作用于正在跑的执行。
    # 引擎在 hooks.check_cancelled 检查点读 flag；跨 worker 正确（flag 共享在 Redis，由
    # 持有该轮的 worker 读取），且 flag 在 RUNNING 期间几秒内即被读取、不会跨越任何
    # Redis 观察不到的等待。
    #
    # 为什么 QUEUED 不允许取消：排队是 worker 本地的 in-memory semaphore 等待，Redis
    # 看不到「谁在排队、在哪个 worker」。让 Redis 中介的 cancel 去够 worker 本地状态，
    # 就得把 cancel flag 跨「Redis 观察不到的等待」续命，制造 cancel 语义撕裂。
    # 排队轮无害、瞬态、很快起跑，起跑后即可取消。故 QUEUED 返回 409（显式
    # best-effort 契约），且**不**置任何 flag。
    try:
        active_msg_id = await execution_service.cancel(conv_id, current_user.user_id)
    except ConversationResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")
    except ExecutionStillQueued:
        raise HTTPException(
            status_code=409,
            detail="Execution is queued (waiting for a concurrency slot); "
                   "it becomes cancellable once it starts running.",
        )
    except NoActiveExecution:
        raise HTTPException(status_code=409, detail="No active execution for this conversation")

    return CancelResponse(message_id=active_msg_id)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runtime_status: RuntimeStatusReader = Depends(get_runtime_status_reader),
):
    """列出对话列表"""
    user_id = current_user.user_id
    title_query = q.strip() if q else None
    total = await conversation_manager.count_conversations_async(user_id=user_id, title_query=title_query)
    conversations = await conversation_manager.list_conversations_async(
        limit=limit, offset=offset, user_id=user_id, title_query=title_query
    )

    # lease 是"运行中"的单一事实源。需要返回 message_id(不是 bool)是因为
    # 前端要用它做 compare-and-clear:terminal SSE 携带 message_id,缓存
    # 端持有 active_message_id,只有两者相等才清。bool 模式下旧 turn 的
    # terminal 会误清新 turn 的指示点(详见 ConversationSummary 注释)。
    # RuntimeStore 不持有 user_id,但返回的 conv_id 与本用户列表求交后天
    # 然只命中当前用户自己的会话。
    active_executions = await runtime_status.list_active_executions()

    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                id=conv["conversation_id"],
                title=conv.get("title"),
                message_count=conv.get("message_count", 0),
                created_at=datetime.fromisoformat(conv["created_at"]),
                updated_at=datetime.fromisoformat(conv["updated_at"]),
                active_message_id=active_executions.get(conv["conversation_id"]),
                upload_bytes=conv.get("upload_bytes", 0),
            )
            for conv in conversations
        ],
        total=total,
        has_more=offset + len(conversations) < total,
    )


@router.get("/storage", response_model=StorageUsageResponse)
async def get_storage_usage(
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """当前用户的附件存储用量 + 配额（喂前端进度条）。

    与上传挡板同口径（compute-on-read，单一数据源）。声明在 `/{conv_id}` 之前，
    否则 `storage` 会被当作 conv_id 命中详情路由。
    """
    used_bytes = await conversation_manager.get_user_upload_bytes(current_user.user_id)
    return StorageUsageResponse(
        used_bytes=used_bytes,
        quota_bytes=config.ARTIFACT_USER_QUOTA_BYTES,
    )


@router.put(
    "/{conv_id}/messages/{msg_id}/feedback",
    response_model=MessageFeedbackResponse,
)
async def put_message_feedback(
    conv_id: str,
    msg_id: str,
    request: MessageFeedbackRequest,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Create or replace the current user's feedback for one assistant response."""
    feedback = await conversation_manager.set_message_feedback(
        conversation_id=conv_id,
        message_id=msg_id,
        user_id=current_user.user_id,
        rating=request.rating,
        tags=list(request.tags),
        detail=request.detail,
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return MessageFeedbackResponse.model_validate(feedback)


@router.delete(
    "/{conv_id}/messages/{msg_id}/feedback",
    status_code=204,
)
async def delete_message_feedback(
    conv_id: str,
    msg_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Remove feedback idempotently; cross-user/mismatched messages stay hidden."""
    deleted = await conversation_manager.delete_message_feedback(
        conversation_id=conv_id,
        message_id=msg_id,
        user_id=current_user.user_id,
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return Response(status_code=204)


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
                    created_at=msg.created_at,
                    children=children_map.get(msg.id, []),
                    feedback=(
                        MessageFeedbackResponse.model_validate(msg.feedback)
                        if msg.feedback is not None
                        else None
                    ),
                    execution_metrics=(msg.metadata_ or {}).get("execution_metrics"),
                    uploaded_files=(msg.metadata_ or {}).get("uploaded_files"),
                    activated_skills=(msg.metadata_ or {}).get("activated_skills"),
                    referenced_artifacts=(msg.metadata_ or {}).get("referenced_artifacts"),
                    active_skills=(
                        ((msg.metadata_ or {}).get("agent_progressive_state") or {})
                        .get("lead_agent", {})
                        .get("active_skills")
                    ),
                )
                for msg in messages
            ],
            session_id=conv_id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    except ConversationResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


@router.delete(
    "/{conv_id}",
    responses={
        409: {
            "model": ErrorResponse,
            "description": "Conversation has an active execution",
        }
    },
)
async def delete_conversation(
    conv_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    execution_service: ConversationExecutionService = Depends(
        get_conversation_execution_service
    ),
):
    """删除对话"""
    try:
        await execution_service.delete(conv_id, current_user.user_id)

        return {"success": True, "message": f"Conversation '{conv_id}' deleted"}

    except ConversationExecutionConflict:
        raise HTTPException(
            status_code=409,
            detail=(
                "Conversation has an active execution. Wait for it to finish, "
                "or cancel it once running, before deleting."
            ),
        )
    except ConversationAdmissionUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Conversation delete could not confirm ownership; please retry.",
        )
    except ConversationResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_conversations(
    request: BulkDeleteRequest,
    current_user: TokenPayload = Depends(get_current_user),
    execution_service: ConversationExecutionService = Depends(
        get_conversation_execution_service
    ),
):
    """
    批量删除对话（用户视角，仅删自己的）

    Best-effort 范围：cross-user / 不存在的 id 走 `failed.reason="not_found"`，
    遵循 "404 not 403" 安全策略避免泄漏会话存在。持有 execution lease
    （含 QUEUED / RUNNING）的会话走 `failed.reason="active_execution"`，不删除。

    单行 FK 违规这条路径不存在，因此不需要 IntegrityError + rollback：所有指向
    `conversations.id` 的外键（Message / ArtifactSession）都是 ondelete=CASCADE，
    下游链 messages / events / artifacts / artifact_versions 也全是 CASCADE
    （src/db/models.py），删 conversation 不会因子行残留而失败，session 状态
    不会被某一行污染到影响后续行。

    其他异常（OperationalError 等基础设施级故障）冒泡为 5xx loud failure；
    此时第 1 条就会失败、循环本就进不下去，故不做无法触发的广泛 except。
    """
    try:
        result = await execution_service.bulk_delete(
            request.ids, current_user.user_id
        )
    except ConversationAdmissionUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Conversation delete could not confirm ownership; please retry.",
        )
    failed = [
        BulkDeleteFailedItem(id=item.conversation_id, reason=item.reason)
        for item in result.failed
    ]
    return BulkDeleteResponse(deleted=result.deleted, failed=failed)


@router.get("/{conv_id}/messages/{msg_id}/events")
async def get_message_events(
    conv_id: str,
    msg_id: str,
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """查询消息的事件链（用于历史回放和可观测性）"""
    await _verify_ownership(conv_id, current_user, conversation_manager)

    # 校验 message 归属
    message = await conversation_manager.get_message(msg_id)
    if not message or message.conversation_id != conv_id:
        raise HTTPException(status_code=404, detail="Message not found")

    events = await conversation_manager.get_message_events(msg_id, event_type=event_type)

    return {
        "message_id": msg_id,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "agent_name": e.agent_name,
                "data": project_event_data_for_user(e.event_type, e.data),
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
    execution_service: ConversationExecutionService = Depends(
        get_conversation_execution_service
    ),
):
    """
    恢复中断的执行（权限确认后）

    通过 RuntimeStore.resolve_interrupt() 唤醒暂停的 coroutine。
    """
    message_id = request.message_id

    try:
        await execution_service.resume(
            conversation_id=conv_id,
            user_id=current_user.user_id,
            message_id=message_id,
            call_id=request.call_id,
            approved=request.approved,
            always_allow=request.always_allow,
        )
    except ConversationResourceNotFoundError as exc:
        detail = (
            "Message not found"
            if exc.entity_type == "Message"
            else f"Conversation '{conv_id}' not found"
        )
        raise HTTPException(status_code=404, detail=detail)
    except PendingInterruptNotFound:
        raise HTTPException(status_code=404, detail="No pending interrupt found for this message")
    except PendingInterruptStale:
        logger.warning(
            "Resume rejected (409): stale permission call_id "
            f"(conv={conv_id}, message={message_id}, call={request.call_id})"
        )
        raise HTTPException(
            status_code=409,
            detail="Permission request is stale; a different tool call is awaiting approval",
        )
    except PendingInterruptAlreadyResolved:
        raise HTTPException(status_code=409, detail="Interrupt already resolved for this message")

    # 不需要创建新 stream — 原来的 coroutine 继续执行，
    # 事件会继续推送到原来的 stream（使用 message_id 作为 stream key）
    return ResumeResponse(
        stream_url=f"/api/v1/stream/{message_id}"
    )

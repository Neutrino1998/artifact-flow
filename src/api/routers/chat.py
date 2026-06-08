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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import ValidationError

from config import config
from utils.time import utc_now

from api.dependencies import (
    get_conversation_manager,
    get_current_user,
    get_db_session,
    get_stream_transport,
    get_execution_runner,
)
from api.services.auth import TokenPayload
from api.schemas.chat import (
    BulkDeleteFailedItem,
    BulkDeleteRequest,
    BulkDeleteResponse,
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
from api.services.runtime_store import InjectQueueFull
from api.routers.artifacts import convert_uploaded_file
from core.conversation_manager import ConversationManager
from core.events import StreamEventType
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
    payload: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_transport: StreamTransport = Depends(get_stream_transport),
    runner: ExecutionRunner = Depends(get_execution_runner),
):
    """
    发送新消息（multipart/form-data）

    `payload` 为 ChatRequest 的 JSON 字符串；`files` 为可选附件。附件在起 turn
    前同步转成 artifact（source=user_upload）落库，并把 (id, filename) 透传进
    USER_INPUT 事件正文，让 agent 知道哪些是本轮新传的。返回 stream_url 供前端订阅。
    """
    from uuid import uuid4

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
    # 转换 + DB 写入 + USER_INPUT 归属串膨胀。每个文件的 20MB 大小限制仍在转换处生效。
    attachment_count = sum(1 for f in files if f.filename)
    if attachment_count > config.MAX_CHAT_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many attachments: {attachment_count} (max {config.MAX_CHAT_ATTACHMENTS})",
        )

    # 空白正文且无附件 = 本轮无可处理输入：USER_INPUT 正文为空 → 被 EventHistory 过滤
    # → history 为空 → build() 在 [-1] 崩。边界即拒（前端 sendDisabled 同条件，这里是
    # 非 UI 客户端的兜底）；带附件时由归属串补足正文，故仅无附件时要求非空。
    # force_compact 与附件同理：execute_loop 会向 USER_INPUT 正文注入压缩指令（非空），
    # 故「点压缩但不打字」的纯压缩轮次同样放行。
    if not request.user_input.strip() and attachment_count == 0 and not request.force_compact:
        raise HTTPException(
            status_code=422,
            detail="user_input must not be blank when no files are attached",
        )

    # 为新消息准备 ID
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = f"conv-{uuid4().hex}"

    message_id = f"msg-{uuid4().hex}"
    user_id = current_user.user_id

    # 已有会话：校验归属（只读检查，尽早拒绝；不在此创建任何行）
    if request.conversation_id:
        await _verify_ownership(conversation_id, current_user, conversation_manager)

    # 附件:相一 **纯转换**（bytes → 文本），不碰 DB、不 commit 任何 artifact。任一附件
    # 格式不支持 / 无法解码 / 转换失败 → 在此抛 422/500，此时 conversation 与 artifact 都
    # 未创建，批次「全有或全无」。转换后的内容 closure-carry 进控制器，由 execute_loop 在
    # turn 起点 stage 进 WorkingSet（统一生命周期：发 ARTIFACT_CREATED、随 turn 末 flush
    # 落库）——**不在此即时 commit**。
    # 由此「上传即时 commit」退场带来的两个好处:
    #   - `_N` 去重副本 bug 消失:submit 抛 409（已有活跃执行）时尚未 stage 任何东西，
    #     execute_and_push 根本不会跑 → 重发不产生副本（旧实现在 submit 前已 commit）。
    #   - 上传与模型产物的「turn 中途死即丢失」语义一致（皆 ephemeral，随 lease 重启而失）；
    #     用户侧由前端 staged 文件保留到 COMPLETE 兜底。
    converted = [
        await convert_uploaded_file(f)
        for f in files
        if f.filename  # 空 file part（前端无附件时不应出现，防御性跳过）
    ]

    # 确保 conversation 存在（失败需返回 HTTP 错误，保留在路由层；FK: artifact_session
    # → conversation，但 artifact 现在 turn 末才落库，ensure 仍需在 submit 前建好会话行）
    await conversation_manager.ensure_conversation_exists(conversation_id, user_id=user_id)

    # 转换后内容打包 closure-carry 给控制器（不 commit）。
    uploaded_files: List[dict] = [
        {
            "filename": c.filename,
            "content": c.content,
            "content_type": c.content_type,
            "metadata": c.metadata,
            "blob": c.blob,                          # 二进制源(图片/富格式),纯文本为 None
            "blob_content_type": c.blob_content_type,
        }
        for c in converted
    ]

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
                        uploaded_files=uploaded_files,
                        force_compact=request.force_compact,
                        **parent_kwargs,
                    ),
                )
        except Exception as e:
            logger.exception(f"Failed to initialize execution: {e}")
            await stream_transport.push_event(message_id, sanitize_error_event({
                "type": "error",
                "timestamp": utc_now().isoformat(),
                "data": {"success": False, "error": str(e)}
            }))
            await stream_transport.close_stream(message_id)

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

    # inject gate 在 interactive（== RUNNING：semaphore 取得后 → 引擎退出）。
    # 还在排队（持 lease 但未 interactive）的 turn 返回 409 —— inject 的语义是
    # "给一个正在跑的引擎追加输入"，引擎没起跑前没有消费者来 drain 这个队列。
    active_msg_id = await runner.store.get_interactive_message_id(conv_id)
    if not active_msg_id:
        raise HTTPException(status_code=409, detail="No active execution for this conversation")

    try:
        await runner.store.inject_message(active_msg_id, request.content)
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
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runner: ExecutionRunner = Depends(get_execution_runner),
):
    """
    取消活跃执行

    请求取消 conversation 当前正在运行的执行。引擎会在下一个检查点优雅退出。
    """
    await _verify_ownership(conv_id, current_user, conversation_manager)

    # cancel gate 在 interactive（== RUNNING），与 inject 对称：只作用于正在跑的执行。
    # 引擎在 hooks.check_cancelled 检查点读 flag；跨 worker 正确（flag 共享在 Redis，由
    # 持有该轮的 worker 读取），且 flag 在 RUNNING 期间几秒内即被读取、不会跨越任何
    # Redis 观察不到的等待。
    #
    # 为什么 QUEUED 不允许取消：排队是 worker 本地的 in-memory semaphore 等待，Redis
    # 看不到「谁在排队、在哪个 worker」。让 Redis 中介的 cancel 去够 worker 本地状态，
    # 就得把 cancel flag 跨「Redis 观察不到的等待」续命 —— 反复制造 cancel 语义撕裂
    # （HA review r4 round-1/2 同形状反复的根因）。排队轮无害、瞬态、很快起跑，起跑后
    # 即可取消。故 QUEUED 返回 409（显式 best-effort 契约），且**不**置任何 flag。
    active_msg_id = await runner.store.get_interactive_message_id(conv_id)
    if not active_msg_id:
        # 仅为给更清楚的 409 而读一次 lease（只读、不置 flag）：区分「排队中」与「无执行」。
        if await runner.store.get_leased_message_id(conv_id):
            raise HTTPException(
                status_code=409,
                detail="Execution is queued (waiting for a concurrency slot); "
                       "it becomes cancellable once it starts running.",
            )
        raise HTTPException(status_code=409, detail="No active execution for this conversation")

    await runner.store.request_cancel(active_msg_id)

    return CancelResponse(message_id=active_msg_id)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runner: ExecutionRunner = Depends(get_execution_runner),
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
    active_executions = await runner.store.list_active_executions()

    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                id=conv["conversation_id"],
                title=conv.get("title"),
                message_count=conv.get("message_count", 0),
                created_at=datetime.fromisoformat(conv["created_at"]),
                updated_at=datetime.fromisoformat(conv["updated_at"]),
                active_message_id=active_executions.get(conv["conversation_id"]),
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
                    created_at=msg.created_at,
                    children=children_map.get(msg.id, []),
                    execution_metrics=(msg.metadata_ or {}).get("execution_metrics"),
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


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_conversations(
    request: BulkDeleteRequest,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    批量删除对话（用户视角，仅删自己的）

    Best-effort 范围：cross-user / 不存在的 id 走 `failed.reason="not_found"`，
    遵循 "404 not 403" 安全策略避免泄漏会话存在。引擎正在执行的会话同样直接
    DELETE — 引擎 post-processing 在 PR2a 里 fail-soft 兜底。

    单行 FK 违规这条路径不存在，因此不需要 IntegrityError + rollback：所有指向
    `conversations.id` 的外键（Message / ArtifactSession）都是 ondelete=CASCADE，
    下游链 messages / events / artifacts / artifact_versions 也全是 CASCADE
    （src/db/models.py），删 conversation 不会因子行残留而失败，session 状态
    不会被某一行污染到影响后续行。

    其他异常（OperationalError 等基础设施级故障）冒泡为 5xx loud failure —
    此时第 1 条就会失败、循环本就进不下去；与 CLAUDE.md "不为不会发生的场景
    加防御代码" 一致，故不做广泛 except。
    """
    user_id = current_user.user_id
    deleted: list[str] = []
    failed: list[BulkDeleteFailedItem] = []
    seen: set[str] = set()

    for conv_id in request.ids:
        if conv_id in seen:
            continue
        seen.add(conv_id)

        try:
            if not await conversation_manager.verify_ownership(conv_id, user_id):
                failed.append(BulkDeleteFailedItem(id=conv_id, reason="not_found"))
                continue

            success = await conversation_manager.delete_conversation(conv_id)
            if success:
                deleted.append(conv_id)
            else:
                failed.append(BulkDeleteFailedItem(id=conv_id, reason="not_found"))
        except NotFoundError:
            failed.append(BulkDeleteFailedItem(id=conv_id, reason="not_found"))

    return BulkDeleteResponse(deleted=deleted, failed=failed)


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
                "data": _replay_safe_event_data(e.event_type, e.data),
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "total": len(events),
    }


def _replay_safe_event_data(event_type: str, data):
    """Replay 读边界脱敏:prod 下不重新暴露 raw 内部错误,对齐 live 的
    sanitize_error_event。DB 仍存 raw(审计 / DEBUG replay 可见);request_id
    已随事件持久化,保留不动(用户凭它回传、运维凭它 grep)。仅用户端点生效——
    admin 可观测端点不脱敏(管理员应看到真实错误)。"""
    if (
        not config.DEBUG
        and event_type == StreamEventType.ERROR.value
        and isinstance(data, dict)
        and data.get("error")
    ):
        return {**data, "error": "Internal server error"}
    return data


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



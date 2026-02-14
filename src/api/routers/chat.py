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
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from api.config import config
from api.dependencies import (
    get_conversation_manager,
    get_stream_manager,
    get_task_manager,
)
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
from api.services.stream_manager import StreamManager
from api.services.task_manager import TaskManager
from core.conversation_manager import ConversationManager
from repositories.base import NotFoundError
from utils.logger import get_logger, set_request_context

logger = get_logger("ArtifactFlow")

router = APIRouter()


def _sanitize_error_event(event: dict) -> dict:
    """Strip internal error details from error events in production."""
    if config.DEBUG:
        return event
    if event.get("type") == "error" and isinstance(event.get("data"), dict):
        event = {**event, "data": {**event["data"], "error": "Internal server error"}}
    return event


@router.post("", response_model=ChatResponse)
async def send_message(
    request: ChatRequest,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_manager: StreamManager = Depends(get_stream_manager),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    发送新消息

    启动 Graph 执行，返回 stream_url 供前端订阅。

    流程：
    1. 同步创建/获取 conversation（确保 GET 请求能立即看到）
    2. 创建 StreamContext（开始缓冲事件）
    3. 启动后台任务执行 Graph
    4. 返回 stream_url 给前端
    """
    # 生成 thread_id
    from uuid import uuid4
    thread_id = f"thd-{uuid4().hex}"

    # 为新消息准备 conversation_id 和 message_id
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = f"conv-{uuid4().hex}"

    message_id = f"msg-{uuid4().hex}"

    # 1. 同步创建/确保 conversation 存在（在请求的 session 中，会自动 commit）
    # 这确保后续的 GET 请求能立即看到这个 conversation
    await conversation_manager.ensure_conversation_exists(conversation_id)

    # 2. 创建 stream
    await stream_manager.create_stream(thread_id)

    # 3. 设置请求上下文（background task 通过 asyncio context 继承自动获取）
    set_request_context(thread_id=thread_id, conv_id=conversation_id)

    # 4. 启动后台任务
    # 注意：不能直接用 BackgroundTasks，因为依赖（controller）会在请求结束后失效
    # 需要创建一个独立的任务
    async def execute_and_push():
        """
        独立的执行任务

        重新获取 controller 以避免依赖生命周期问题
        """
        from api.dependencies import (
            get_db_manager,
            get_checkpointer,
        )
        from core.graph import create_multi_agent_graph
        from core.controller import ExecutionController
        from core.conversation_manager import ConversationManager
        from tools.implementations.artifact_ops import ArtifactManager
        from repositories.artifact_repo import ArtifactRepository
        from repositories.conversation_repo import ConversationRepository

        db_manager = get_db_manager()

        async with db_manager.session() as session:
            # 创建请求级别的依赖
            artifact_repo = ArtifactRepository(session)
            artifact_manager = ArtifactManager(artifact_repo)

            conv_repo = ConversationRepository(session)
            conv_manager = ConversationManager(conv_repo)

            # 创建 Graph 和 Controller
            compiled_graph = await create_multi_agent_graph(
                artifact_manager=artifact_manager,
                checkpointer=get_checkpointer()
            )

            ctrl = ExecutionController(
                compiled_graph,
                artifact_manager=artifact_manager,
                conversation_manager=conv_manager
            )

            # 执行并推送事件
            # Graph 执行独立于 SSE 连接：即使前端断开，graph 仍运行到完成，结果持久化到数据库
            stream_closed = False
            try:
                async with asyncio.timeout(config.STREAM_TIMEOUT):
                    # Pass parent_message_id only when explicitly provided in request;
                    # otherwise let controller auto-detect via active_branch
                    parent_kwargs = {}
                    if 'parent_message_id' in request.model_fields_set:
                        parent_kwargs['parent_message_id'] = request.parent_message_id
                    async for event in ctrl.stream_execute(
                        content=request.content,
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        message_id=message_id,
                        **parent_kwargs,
                    ):
                        if stream_closed:
                            continue
                        if not await stream_manager.push_event(thread_id, _sanitize_error_event(event)):
                            logger.info(f"Stream {thread_id} closed, graph will continue to completion")
                            stream_closed = True

            except TimeoutError:
                logger.error(f"Graph execution timed out after {config.STREAM_TIMEOUT}s for thread {thread_id}")
                await stream_manager.push_event(thread_id, {
                    "type": "error",
                    "timestamp": datetime.now().isoformat(),
                    "data": {"success": False, "error": f"Execution timed out after {config.STREAM_TIMEOUT}s"}
                })

            except Exception as e:
                logger.exception(f"Error in graph execution: {e}")
                await stream_manager.push_event(thread_id, _sanitize_error_event({
                    "type": "error",
                    "timestamp": datetime.now().isoformat(),
                    "data": {"success": False, "error": str(e)}
                }))

    # 5. 提交到 TaskManager（持有引用 + 并发控制）
    await task_manager.submit(thread_id, execute_and_push())

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        thread_id=thread_id,
        stream_url=f"/api/v1/stream/{thread_id}"
    )


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    列出对话列表

    Args:
        limit: 每页数量
        offset: 偏移量
    """
    conversations = await conversation_manager.list_conversations_async(
        limit=limit + 1,  # 多取一条用于判断 has_more
        offset=offset
    )

    has_more = len(conversations) > limit
    if has_more:
        conversations = conversations[:limit]

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
        total=offset + len(conversations) + (1 if has_more else 0),
        has_more=has_more,
    )


@router.get("/{conv_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conv_id: str,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    获取对话详情（含消息树）
    """
    try:
        repo = conversation_manager._ensure_repository()

        # 直接从数据库获取对话（不尝试创建）
        conversation = await repo.get_conversation(conv_id, load_messages=True)
        if not conversation:
            raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")

        # 获取所有消息
        messages = await repo.get_conversation_messages(conv_id)

        # 构建子消息关系
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
                    response=msg.graph_response,
                    created_at=msg.created_at,
                    children=children_map.get(msg.id, []),
                )
                for msg in messages
            ],
            session_id=conv_id,  # session_id 与 conversation_id 相同
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: str,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    删除对话

    级联删除消息和关联的 Artifacts。
    """
    try:
        repo = conversation_manager._ensure_repository()
        success = await repo.delete_conversation(conv_id)

        if not success:
            raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")

        # 清除缓存
        conversation_manager.clear_cache(conv_id)

        return {"success": True, "message": f"Conversation '{conv_id}' deleted"}

    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conv_id}' not found")


@router.post("/{conv_id}/resume", response_model=ResumeResponse)
async def resume_execution(
    conv_id: str,
    request: ResumeRequest,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    stream_manager: StreamManager = Depends(get_stream_manager),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    恢复中断的执行（权限确认后）

    流程：
    1. 校验 thread_id 归属
    2. 创建新的 StreamContext
    3. 启动后台任务恢复执行
    4. 返回新的 stream_url
    """
    thread_id = request.thread_id

    # 1. 校验 thread_id 归属当前 conversation
    repo = conversation_manager._ensure_repository()
    message = await repo.get_message(request.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.thread_id != thread_id or message.conversation_id != conv_id:
        raise HTTPException(status_code=403, detail="Thread does not belong to this conversation")

    # 2. 创建新的 stream（可能使用相同的 thread_id）
    try:
        await stream_manager.create_stream(thread_id)
    except Exception:
        # 如果 stream 已存在，先关闭再创建
        await stream_manager.close_stream(thread_id)
        await stream_manager.create_stream(thread_id)

    # 准备恢复数据
    resume_data = {
        "type": "permission",
        "approved": request.approved
    }

    # 设置请求上下文（background task 通过 asyncio context 继承自动获取）
    set_request_context(thread_id=thread_id, conv_id=conv_id)

    # 启动后台任务
    async def execute_resume():
        from api.dependencies import (
            get_db_manager,
            get_checkpointer,
        )
        from core.graph import create_multi_agent_graph
        from core.controller import ExecutionController
        from core.conversation_manager import ConversationManager
        from tools.implementations.artifact_ops import ArtifactManager
        from repositories.artifact_repo import ArtifactRepository
        from repositories.conversation_repo import ConversationRepository

        db_manager = get_db_manager()

        async with db_manager.session() as session:
            artifact_repo = ArtifactRepository(session)
            artifact_manager = ArtifactManager(artifact_repo)

            conv_repo = ConversationRepository(session)
            conv_manager = ConversationManager(conv_repo)

            compiled_graph = await create_multi_agent_graph(
                artifact_manager=artifact_manager,
                checkpointer=get_checkpointer()
            )

            ctrl = ExecutionController(
                compiled_graph,
                artifact_manager=artifact_manager,
                conversation_manager=conv_manager
            )

            # Graph 执行独立于 SSE 连接：即使前端断开，graph 仍运行到完成，结果持久化到数据库
            stream_closed = False
            try:
                async with asyncio.timeout(config.STREAM_TIMEOUT):
                    async for event in ctrl.stream_execute(
                        thread_id=thread_id,
                        conversation_id=conv_id,
                        message_id=request.message_id,
                        resume_data=resume_data,
                    ):
                        if stream_closed:
                            continue
                        if not await stream_manager.push_event(thread_id, _sanitize_error_event(event)):
                            logger.info(f"Stream {thread_id} closed, graph will continue to completion")
                            stream_closed = True

            except TimeoutError:
                logger.error(f"Resume execution timed out after {config.STREAM_TIMEOUT}s for thread {thread_id}")
                await stream_manager.push_event(thread_id, {
                    "type": "error",
                    "timestamp": datetime.now().isoformat(),
                    "data": {"success": False, "error": f"Execution timed out after {config.STREAM_TIMEOUT}s"}
                })

            except Exception as e:
                logger.exception(f"Error in resume execution: {e}")
                await stream_manager.push_event(thread_id, _sanitize_error_event({
                    "type": "error",
                    "timestamp": datetime.now().isoformat(),
                    "data": {"success": False, "error": str(e)}
                }))

    await task_manager.submit(thread_id, execute_resume())

    return ResumeResponse(
        stream_url=f"/api/v1/stream/{thread_id}"
    )

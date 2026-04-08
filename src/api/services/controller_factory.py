"""
Controller Factory — ExecutionController 组装 + 执行推送

从 chat.py 提取，将 controller 组装和执行推送逻辑与路由层解耦。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator, AsyncIterator

from config import config
from api.dependencies import (
    get_agents,
    get_compaction_manager,
    get_db_manager,
    get_execution_runner,
    get_tools,
)
from api.services.stream_transport import StreamTransport
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


def sanitize_error_event(event: dict) -> dict:
    """Strip internal error details from error events in production."""
    if config.DEBUG:
        return event
    if event.get("type") == "error" and isinstance(event.get("data"), dict):
        event = {**event, "data": {**event["data"], "error": "Internal server error"}}
    return event


@asynccontextmanager
async def create_controller(conversation_id: str, message_id: str) -> AsyncGenerator:
    """
    Build a fresh ExecutionController with its own DB session.

    Why not use Depends(get_controller)?
    send_message() launches a background task whose lifetime exceeds the HTTP request.
    Depends(get_db_session) closes the session when the request ends, but the background
    task still needs a live session. This context manager provides an independent session
    scoped to the background task's lifetime.

    Usage:
        async with create_controller(conv_id, msg_id) as ctrl:
            async for event in ctrl.stream_execute(...):
                ...
    """
    from core.controller import ExecutionController
    from core.engine import EngineHooks
    from core.conversation_manager import ConversationManager as CM
    from tools.builtin.artifact_ops import ArtifactManager, create_artifact_tools
    from repositories.artifact_repo import ArtifactRepository
    from repositories.conversation_repo import ConversationRepository as CR
    from repositories.message_event_repo import MessageEventRepository

    db_manager = get_db_manager()
    runner = get_execution_runner()
    store = runner.store
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

        hooks = EngineHooks(
            check_cancelled=store.is_cancelled,
            wait_for_interrupt=store.wait_for_interrupt,
            drain_messages=store.drain_messages,
        )

        async def _on_engine_exit(conv_id: str, msg_id: str) -> None:
            await store.clear_engine_interactive(conv_id, msg_id)

        yield ExecutionController(
            agents=agents,
            tools=all_tools,
            hooks=hooks,
            artifact_manager=artifact_manager,
            conversation_manager=conv_manager,
            message_event_repo=event_repo,
            compaction_manager=get_compaction_manager(),
            on_engine_exit=_on_engine_exit,
            db_manager=db_manager,
        )


async def run_and_push(
    stream_transport: StreamTransport,
    stream_id: str,
    event_stream: AsyncIterator[dict],
) -> None:
    """
    Consume events from a controller stream and push them to the StreamTransport.

    Handles timeout and unexpected errors, pushing sanitized error events.
    Execution runs to completion even if the SSE client disconnects.
    Stream is always closed by the producer in the finally block.
    """
    stream_closed = False
    # llm_chunk coalescing: 保留最新快照，定时 flush（80ms）
    pending_chunks: dict[str, dict] = {}  # "content" | "reasoning_content" → latest event
    last_flush_time = 0.0

    async def flush_pending():
        nonlocal last_flush_time, stream_closed
        for key in list(pending_chunks):
            if stream_closed:
                pending_chunks.clear()
                return
            if not await stream_transport.push_event(stream_id, sanitize_error_event(pending_chunks.pop(key))):
                logger.info(f"Stream {stream_id} closed, execution continues")
                stream_closed = True
        last_flush_time = asyncio.get_event_loop().time()

    try:
        async with asyncio.timeout(config.EXECUTION_TIMEOUT):
            async for event in event_stream:
                if stream_closed:
                    continue

                if event.get("type") == "llm_chunk":
                    data = event.get("data", {})
                    chunk_key = "reasoning_content" if "reasoning_content" in data else "content"
                    pending_chunks[chunk_key] = event
                    now = asyncio.get_event_loop().time()
                    if now - last_flush_time >= 0.08:
                        await flush_pending()
                else:
                    await flush_pending()  # 非 chunk 前先 flush
                    if stream_closed:
                        continue
                    if not await stream_transport.push_event(stream_id, sanitize_error_event(event)):
                        logger.info(f"Stream {stream_id} closed, execution continues")
                        stream_closed = True

            await flush_pending()  # 流结束 flush 残余

    except TimeoutError:
        logger.error(f"Execution timed out after {config.EXECUTION_TIMEOUT}s for {stream_id}")
        await stream_transport.push_event(stream_id, sanitize_error_event({
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "data": {"success": False, "error": f"Execution timed out after {config.EXECUTION_TIMEOUT}s"}
        }))

    except Exception as e:
        logger.exception(f"Error in execution: {e}")
        await stream_transport.push_event(stream_id, sanitize_error_event({
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "data": {"success": False, "error": str(e)}
        }))

    finally:
        # Producer 侧关闭 stream — 设 closed 状态 + 延迟清理 TTL
        await stream_transport.close_stream(stream_id)

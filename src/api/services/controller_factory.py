"""
Controller Factory — ExecutionController 组装 + 执行推送

从 chat.py 提取，将 controller 组装和执行推送逻辑与路由层解耦。
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, AsyncIterator

from config import config
from api.dependencies import (
    get_agents,
    get_db_manager,
    get_execution_runner,
    get_tools,
)
from api.services.stream_transport import StreamTransport
from utils.logger import get_logger, get_request_id
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


def sanitize_error_event(event: dict) -> dict:
    """脱敏 error 事件并注入 request_id 定位码。

    request_id 不论 DEBUG 都注入(prod 回传给用户安全,是可回传的错误码);
    脱敏只删 error 文本,绝不删定位码。request_id 取自当前 context —— 后台
    引擎任务由 chat 请求 create_task 起,会继承发起请求的 request_id。
    """
    if event.get("type") != "error" or not isinstance(event.get("data"), dict):
        return event
    data = {**event["data"]}
    req_id = get_request_id()
    if req_id and not data.get("request_id"):
        data["request_id"] = req_id
    if not config.DEBUG:
        data["error"] = "Internal server error"
    return {**event, "data": data}


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
    from tools.builtin.artifact_service import ArtifactService
    from tools.builtin.artifact_ops import create_artifact_tools
    from tools.builtin.sandbox_session import SandboxSession
    from tools.builtin.sandbox_ops import create_sandbox_tools
    from repositories.artifact_repo import ArtifactRepository
    from repositories.conversation_repo import ConversationRepository as CR
    from repositories.message_event_repo import MessageEventRepository

    db_manager = get_db_manager()
    runner = get_execution_runner()
    store = runner.store
    agents = get_agents()

    # per-turn 沙盒 session:对象壳在此创建(同 ArtifactService,构造注入工具),
    # 容器 lazy 于首个沙盒工具调用 —— 无沙盒 turn 壳零成本。拆除句柄注册进
    # runner,在 _wrapped 真 finally(cleanup_execution 旁)执行,与 lease 同生灭;
    # close 幂等不依赖 DB session,故晚于本 context manager 退出也安全。
    sandbox_session = SandboxSession(conversation_id, message_id)
    runner.register_cleanup(message_id, sandbox_session.close)

    async with db_manager.session() as session:
        artifact_repo = ArtifactRepository(session)
        artifact_service = ArtifactService(artifact_repo)

        # 合并全局工具 + 请求级 artifact / 沙盒工具
        artifact_tools = create_artifact_tools(artifact_service)
        sandbox_tools = create_sandbox_tools(sandbox_session)
        all_tools = {
            **get_tools(),
            **{t.name: t for t in artifact_tools},
            **{t.name: t for t in sandbox_tools},
        }

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
            artifact_service=artifact_service,
            conversation_manager=conv_manager,
            message_event_repo=event_repo,
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

    # 纯转发器:不再裹 asyncio.timeout —— 超时裁判已下沉到 controller 的 engine_task
    # (run_engine 的 asyncio.timeout(EXECUTION_TIMEOUT) → TIMED_OUT 终态),与 DB 终态
    # 同源、经同一个 decide_terminal dispatcher 产出。这里只转发包括 TIMED_OUT 在内的
    # 所有事件。(后处理的 wall-clock 上界由 DB 层负责,见 controller.run_engine 注释。)
    try:
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

    except Exception as e:
        logger.exception(f"Error in execution: {e}")
        await stream_transport.push_event(stream_id, sanitize_error_event({
            "type": "error",
            "timestamp": utc_now().isoformat(),
            "data": {"success": False, "error": str(e)}
        }))

    finally:
        # Producer 侧关闭 stream — 设 closed 状态 + 延迟清理 TTL
        await stream_transport.close_stream(stream_id)

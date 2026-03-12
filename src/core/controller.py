"""
执行控制器（v2 — 无 LangGraph）

职责：
1. stream_execute() — 创建 state → 启动 execute_loop → 事件推 StreamManager
2. resume() — 唤醒暂停的 coroutine（不再是"重新调用 graph"）
3. 对话管理复用 ConversationManager
"""

import asyncio
from typing import Dict, Optional, Any, AsyncGenerator
from uuid import uuid4
from datetime import datetime

from core.state import create_initial_state
from core.engine import execute_loop
from core.events import StreamEventType, ExecutionEvent
from core.conversation_manager import ConversationManager
from tools.implementations.artifact_ops import ArtifactManager
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# Sentinel to distinguish "not provided" from "explicitly None"
_UNSET = object()

# Sentinel to signal end of event queue
_SENTINEL = object()


class ExecutionController:
    """
    执行控制器（v2 — Pi-style while loop）

    变化点：
    - 不再持有 compiled_graph
    - 持有 agents（dict[str, AgentConfig]）和 tool_registry
    - resume 是唤醒 coroutine 而非重新调用 graph
    """

    def __init__(
        self,
        agents: Dict[str, Any],           # {name: AgentConfig}
        tool_registry: Any,                # ToolRegistry
        task_manager: Any,                 # TaskManager
        artifact_manager: Optional[ArtifactManager] = None,
        conversation_manager: Optional[ConversationManager] = None,
        message_event_repo: Optional[Any] = None,  # MessageEventRepository
        request_tools: Optional[Dict[str, Any]] = None,  # {name: BaseTool}
        permission_timeout: int = 300,
    ):
        self.agents = agents
        self.tool_registry = tool_registry
        self.task_manager = task_manager
        self.artifact_manager = artifact_manager
        self.conversation_manager = conversation_manager or ConversationManager()
        self.message_event_repo = message_event_repo
        self.request_tools = request_tools
        self.permission_timeout = permission_timeout

        logger.info("ExecutionController v2 initialized")

    async def stream_execute(
        self,
        content: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Any = _UNSET,
        message_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行接口（新消息）

        Args:
            content: 用户消息内容
            conversation_id: 对话ID
            parent_message_id: 父消息ID
            message_id: 消息ID

        Yields:
            流式事件字典
        """
        if content is None:
            raise ValueError("'content' is required for new message execution")

        # ========== 准备工作 ==========
        if not conversation_id:
            conversation_id = await self.conversation_manager.start_conversation_async()
        else:
            await self.conversation_manager.ensure_conversation_exists(conversation_id)

        # Auto-detect parent
        if parent_message_id is _UNSET:
            parent_message_id = await self.conversation_manager.get_active_branch(conversation_id)
            if parent_message_id:
                logger.debug(f"Auto-set parent_message_id to active_branch: {parent_message_id}")

        resolved_parent: Optional[str] = parent_message_id if isinstance(parent_message_id, str) else None

        # History
        if parent_message_id is not _UNSET and resolved_parent is None:
            conversation_history = []
        else:
            conversation_history = await self.conversation_manager.format_conversation_history_async(
                conv_id=conversation_id,
                to_message_id=resolved_parent
            )

        # 生成 ID
        message_id = message_id or f"msg-{uuid4().hex}"

        # Session
        session_id = conversation_id  # session_id = conversation_id

        # 设置 artifact session
        if self.artifact_manager:
            self.artifact_manager.set_session(session_id)
            try:
                await self.artifact_manager.clear_temporary_artifacts(session_id)
            except Exception as e:
                logger.warning(f"Failed to clear temporary artifacts: {e}")

        # 从父消息 metadata 中恢复 always_allowed_tools
        parent_always_allowed = []
        if resolved_parent:
            parent_meta = await self.conversation_manager.get_message_metadata_async(resolved_parent)
            parent_always_allowed = parent_meta.get("always_allowed_tools", [])

        # 创建初始状态
        initial_state = create_initial_state(
            task=content,
            session_id=session_id,
            message_id=message_id,
            conversation_history=conversation_history,
            always_allowed_tools=parent_always_allowed,
        )

        logger.info(f"Processing new message (streaming) in conversation {conversation_id}")

        # 添加消息到 conversation
        await self.conversation_manager.add_message_async(
            conv_id=conversation_id,
            message_id=message_id,
            content=content,
            parent_id=resolved_parent,
        )

        # 先发送元数据事件
        yield {
            "type": StreamEventType.METADATA.value,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "conversation_id": conversation_id,
                "message_id": message_id,
            }
        }

        # ========== 执行引擎 ==========
        event_queue: asyncio.Queue = asyncio.Queue()
        final_state = None

        async def emit_to_queue(event_dict):
            """emit callback: 实时推送事件到 queue"""
            await event_queue.put(event_dict)

        async def run_engine():
            nonlocal final_state
            try:
                final_state = await execute_loop(
                    state=initial_state,
                    agents=self.agents,
                    tool_registry=self.tool_registry,
                    task_manager=self.task_manager,
                    artifact_manager=self.artifact_manager,
                    emit=emit_to_queue,
                    permission_timeout=self.permission_timeout,
                    request_tools=self.request_tools,
                )
            except Exception as e:
                logger.exception(f"Engine error: {e}")
                # Mark error on initial_state (final_state is still None at this point)
                initial_state["error"] = True
                initial_state["response"] = f"Engine error: {str(e)}"
                await event_queue.put({
                    "type": StreamEventType.ERROR.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": False,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "error": str(e),
                    }
                })
            finally:
                await event_queue.put(_SENTINEL)

        engine_task = asyncio.create_task(run_engine())

        try:
            # Yield events in real-time as they arrive
            while True:
                event = await event_queue.get()
                if event is _SENTINEL:
                    break
                yield event
        finally:
            if not engine_task.done():
                await engine_task

        # ========== Post-processing ==========
        # Use initial_state as fallback if engine crashed before setting final_state
        if final_state is None:
            final_state = initial_state

        try:
            response = final_state.get("response", "")
            has_error = final_state.get("error", False)

            # 更新 conversation response
            await self.conversation_manager.update_response_async(
                conv_id=conversation_id,
                message_id=message_id,
                response=response if not has_error else (response or "An error occurred during execution."),
            )

            logger.info("Streaming execution completed")

            # 持久化 always_allowed_tools 到 message metadata
            always_allowed = final_state.get("always_allowed_tools", [])
            if always_allowed:
                await self.conversation_manager.update_message_metadata_async(
                    conv_id=conversation_id,
                    message_id=message_id,
                    metadata={"always_allowed_tools": always_allowed},
                )

            # 构建终态事件
            if has_error:
                terminal_event_dict = {
                    "type": StreamEventType.ERROR.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": False,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "error": response or "Execution failed",
                    }
                }
            else:
                terminal_event_dict = {
                    "type": StreamEventType.COMPLETE.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": True,
                        "interrupted": False,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "response": response,
                        "execution_metrics": final_state.get("execution_metrics", {}),
                    }
                }

            # 终态事件入库（Fix 6: 在 _persist_events 之前追加）
            final_state["events"].append(ExecutionEvent(
                event_type=terminal_event_dict["type"],
                agent_name=None,
                data=terminal_event_dict["data"],
            ))

            # 持久化事件（batch write — 设计文档 §关键设计约束）
            await self._persist_events(message_id, final_state)

            # 发送终态事件到 SSE
            yield terminal_event_dict

        except Exception as e:
            logger.exception(f"Error in post-processing: {e}")
            yield {
                "type": StreamEventType.ERROR.value,
                "timestamp": datetime.now().isoformat(),
                "data": {
                    "success": False,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "error": str(e),
                }
            }

    async def _persist_events(self, message_id: str, final_state: Dict[str, Any]) -> None:
        """
        持久化事件到 MessageEvent 表

        只在两个时刻调用（设计文档 §关键设计约束）：
        1. execution_complete — 成功
        2. error — 失败
        """
        if not self.message_event_repo:
            return

        events = final_state.get("events", [])
        if not events:
            return

        try:
            db_events = [
                {
                    "message_id": message_id,
                    "event_type": e.event_type,
                    "agent_name": e.agent_name,
                    "data": e.data,
                    "created_at": e.created_at,
                }
                for e in events
            ]
            await self.message_event_repo.batch_create(db_events)
            logger.info(f"Persisted {len(db_events)} events for message {message_id}")
        except Exception as e:
            # TODO: 事件持久化失败导致数据丢失，当前仅打日志。
            # 考虑重试或将失败事件写入 fallback 存储。
            logger.error(f"Failed to persist events: {e}")


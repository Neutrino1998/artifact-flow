"""
执行控制器（v2 — 无 LangGraph）

职责：
1. stream_execute() — 创建 state → 启动 execute_loop → 事件推 StreamManager
2. resume() — 唤醒暂停的 coroutine（不再是"重新调用 graph"）
3. 对话管理复用 ConversationManager
"""

from typing import Dict, List, Optional, Any, AsyncGenerator
from uuid import uuid4
from datetime import datetime

from core.state import create_initial_state
from core.engine import execute_loop
from core.events import StreamEventType, finalize_metrics
from core.conversation_manager import ConversationManager
from tools.implementations.artifact_ops import ArtifactManager
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# Sentinel to distinguish "not provided" from "explicitly None"
_UNSET = object()


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
    ):
        self.agents = agents
        self.tool_registry = tool_registry
        self.task_manager = task_manager
        self.artifact_manager = artifact_manager
        self.conversation_manager = conversation_manager or ConversationManager()
        self.message_event_repo = message_event_repo

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

        # 创建初始状态
        initial_state = create_initial_state(
            task=content,
            session_id=session_id,
            message_id=message_id,
            conversation_history=conversation_history,
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
        # 收集所有事件用于最后推送 complete
        collected_events = []

        async def emit_and_yield(event_dict):
            """emit callback: 收集事件并标记为可 yield"""
            collected_events.append(event_dict)
            return True  # 继续执行

        try:
            final_state = await execute_loop(
                state=initial_state,
                agents=self.agents,
                tool_registry=self.tool_registry,
                task_manager=self.task_manager,
                artifact_manager=self.artifact_manager,
                emit=emit_and_yield,
            )

            # yield 所有收集的事件
            for event in collected_events:
                yield event

            # 检查最终状态
            response = final_state.get("response", "")

            # 检查是否有 pending interrupt
            interrupt = self.task_manager.get_interrupt(message_id)
            if interrupt and not interrupt.event.is_set():
                # 有未解决的 interrupt — 这种情况不应该发生
                # 因为 engine loop 会 await interrupt
                logger.warning("Unexpected pending interrupt after engine loop")

            # 更新 conversation response
            await self.conversation_manager.update_response_async(
                conv_id=conversation_id,
                message_id=message_id,
                response=response,
            )

            logger.info("Streaming execution completed")

            # 持久化事件（batch write — 设计文档 §关键设计约束）
            await self._persist_events(message_id, final_state)

            # 发送完成事件
            yield {
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

        except Exception as e:
            logger.exception(f"Error in streaming execution: {e}")

            # yield 已收集的事件
            for event in collected_events:
                yield event

            # 更新错误响应
            await self.conversation_manager.update_response_async(
                conv_id=conversation_id,
                message_id=message_id,
                response="An error occurred during execution.",
            )

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
            logger.error(f"Failed to persist events: {e}")

    async def get_conversation_history(self, conversation_id: str) -> List[Dict]:
        """获取对话历史"""
        return await self.conversation_manager.get_conversation_path_async(conversation_id)

    async def list_conversations(self) -> List[Dict]:
        """列出所有对话"""
        return await self.conversation_manager.list_conversations_async()

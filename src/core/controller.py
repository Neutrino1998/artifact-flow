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
from tools.base import BaseTool
from tools.builtin.artifact_ops import ArtifactManager
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
    - 持有 agents（dict[str, AgentConfig]）和 tools（dict[str, BaseTool]）
    - resume 是唤醒 coroutine 而非重新调用 graph
    """

    def __init__(
        self,
        agents: Dict[str, Any],           # {name: AgentConfig}
        tools: Dict[str, BaseTool],        # {name: BaseTool}
        task_manager: Any,                 # TaskManager
        artifact_manager: Optional[ArtifactManager] = None,
        conversation_manager: Optional[ConversationManager] = None,
        message_event_repo: Optional[Any] = None,  # MessageEventRepository
        permission_timeout: int = 300,
        compaction_manager: Optional[Any] = None,
        compaction_config: Optional[Any] = None,
        context_max_chars: int = 80000,
        compaction_preserve_pairs: int = 2,
        tool_interaction_preserve: int = 6,
    ):
        self.agents = agents
        self.tools = tools
        self.task_manager = task_manager
        self.artifact_manager = artifact_manager
        self.conversation_manager = conversation_manager or ConversationManager()
        self.message_event_repo = message_event_repo
        self.permission_timeout = permission_timeout
        self.compaction_manager = compaction_manager
        self.compaction_config = compaction_config
        self.context_max_chars = context_max_chars
        self.compaction_preserve_pairs = compaction_preserve_pairs
        self.tool_interaction_preserve = tool_interaction_preserve

        logger.info("ExecutionController v2 initialized")

    async def stream_execute(
        self,
        user_input: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Any = _UNSET,
        message_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行接口（新消息）

        Args:
            user_input: 用户消息内容
            conversation_id: 对话ID
            parent_message_id: 父消息ID
            message_id: 消息ID

        Yields:
            流式事件字典
        """
        if user_input is None:
            raise ValueError("'user_input' is required for new message execution")

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

        # Wait for any running compaction before loading history
        compaction_waited = False
        if self.compaction_manager:
            compaction_waited = await self.compaction_manager.wait_if_running(conversation_id)

        # History (reads summaries written by compaction if we waited above)
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
            task=user_input,
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
            user_input=user_input,
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

        # Notify frontend if we waited for compaction
        if compaction_waited:
            yield {
                "type": StreamEventType.COMPACTION_WAIT.value,
                "timestamp": datetime.now().isoformat(),
                "data": {"conversation_id": conversation_id, "status": "completed"},
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
                    tools=self.tools,
                    task_manager=self.task_manager,
                    artifact_manager=self.artifact_manager,
                    emit=emit_to_queue,
                    permission_timeout=self.permission_timeout,
                    context_max_chars=self.context_max_chars,
                    compaction_preserve_pairs=self.compaction_preserve_pairs,
                    tool_interaction_preserve=self.tool_interaction_preserve,
                )
            except Exception as e:
                logger.exception(f"Engine error: {e}")
                # Mark error on initial_state (final_state is still None at this point)
                initial_state["error"] = True
                initial_state["response"] = f"Engine error: {str(e)}"
                error_data = {
                    "success": False,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "error": str(e),
                }
                # Persist error event (will be written via _persist_events on initial_state)
                initial_state["events"].append(ExecutionEvent(
                    event_type=StreamEventType.ERROR.value,
                    agent_name=None,
                    data=error_data,
                ))
                await event_queue.put({
                    "type": StreamEventType.ERROR.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": error_data,
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

        # Engine 已退出，不会再 drain 消息 — 立即取消活跃映射，
        # 使 /inject 端点正确返回 409 而非假装成功入队
        self.task_manager.unregister_conversation(conversation_id)

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

            # 合并为一次 metadata 写入
            metadata_updates = {}
            always_allowed = final_state.get("always_allowed_tools", [])
            if always_allowed:
                metadata_updates["always_allowed_tools"] = always_allowed
            execution_metrics = final_state.get("execution_metrics", {})
            if execution_metrics:
                metadata_updates["execution_metrics"] = execution_metrics
            if metadata_updates:
                await self.conversation_manager.update_message_metadata_async(
                    conv_id=conversation_id,
                    message_id=message_id,
                    metadata=metadata_updates,
                )

            # 自动触发 compaction
            if self.compaction_manager and execution_metrics:
                await self.compaction_manager.maybe_trigger(
                    conv_id=conversation_id,
                    message_id=message_id,
                    execution_metrics=execution_metrics,
                    config=self.compaction_config,
                )

            # 终态事件：error 路径由 engine 已发（在 state["events"] 中），
            # controller 只负责 success 路径的 complete 事件。
            if not has_error:
                terminal_event_dict = {
                    "type": StreamEventType.COMPLETE.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": True,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "response": response,
                        "execution_metrics": final_state.get("execution_metrics", {}),
                    }
                }

                # 终态事件入库
                final_state["events"].append(ExecutionEvent(
                    event_type=terminal_event_dict["type"],
                    agent_name=None,
                    data=terminal_event_dict["data"],
                ))

            # 持久化事件（batch write — 设计文档 §关键设计约束）
            await self._persist_events(message_id, final_state)

            # 发送 complete 到 SSE（error 已由 engine 实时推送）
            if not has_error:
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

        策略：3 次指数退避重试，最终失败写入 fallback 日志文件（JSON lines）。
        """
        if not self.message_event_repo:
            return

        events = final_state.get("events", [])
        if not events:
            return

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

        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.message_event_repo.batch_create(db_events)
                logger.info(f"Persisted {len(db_events)} events for message {message_id}")
                return
            except Exception as e:
                # rollback 使 session 恢复可用状态，否则后续重试会触发 PendingRollbackError
                try:
                    await self.message_event_repo.reset()
                except Exception as rollback_err:
                    logger.debug(f"Session rollback failed during retry: {rollback_err}")
                if attempt < max_retries - 1:
                    wait = 2 ** attempt  # 1s, 2s
                    logger.warning(f"Event persistence attempt {attempt + 1} failed, retrying in {wait}s: {e}")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Event persistence failed after {max_retries} attempts: {e}")
                    self._write_fallback_events(message_id, db_events)

    @staticmethod
    def _write_fallback_events(message_id: str, db_events: list) -> None:
        """将失败事件写入 fallback 日志文件（JSON lines），防止数据丢失。"""
        import json
        from pathlib import Path

        fallback_dir = Path("logs")
        fallback_dir.mkdir(exist_ok=True)
        fallback_path = fallback_dir / "events_fallback.jsonl"

        try:
            with open(fallback_path, "a", encoding="utf-8") as f:
                for event in db_events:
                    record = {**event}
                    # datetime → ISO string for JSON serialization
                    if hasattr(record.get("created_at"), "isoformat"):
                        record["created_at"] = record["created_at"].isoformat()
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.warning(f"Wrote {len(db_events)} fallback events for message {message_id} to {fallback_path}")
        except Exception as fallback_err:
            logger.critical(f"Fallback event write also failed for message {message_id}: {fallback_err}")


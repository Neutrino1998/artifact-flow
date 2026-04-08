"""
执行控制器 — Pi-style while loop

职责：
1. stream_execute() — 创建 state → 启动 execute_loop → 事件推 StreamTransport
2. resume() — 唤醒暂停的 coroutine
3. 对话管理复用 ConversationManager
"""

import asyncio
from typing import Awaitable, Callable, Dict, Optional, Any, AsyncGenerator
from uuid import uuid4
from datetime import datetime

from core.engine import EngineHooks, create_initial_state, execute_loop
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
    """Pi-style 执行控制器，驱动 agent/tool 循环并管理 interrupt resume。"""

    def __init__(
        self,
        agents: Dict[str, Any],           # {name: AgentConfig}
        tools: Dict[str, BaseTool],        # {name: BaseTool}
        hooks: EngineHooks,
        artifact_manager: Optional[ArtifactManager] = None,
        conversation_manager: Optional[ConversationManager] = None,
        message_event_repo: Optional[Any] = None,  # MessageEventRepository
        compaction_manager: Optional[Any] = None,
        on_engine_exit: Optional[Callable[[str, str], Awaitable[None]]] = None,
        db_manager: Optional[Any] = None,
    ):
        self.agents = agents
        self.tools = tools
        self.hooks = hooks
        self.artifact_manager = artifact_manager
        self.conversation_manager = conversation_manager or ConversationManager()
        self.message_event_repo = message_event_repo
        self.compaction_manager = compaction_manager
        self._on_engine_exit = on_engine_exit
        self._db_manager = db_manager
        logger.info("ExecutionController initialized")

    async def _with_db_retry(self, fn):
        """
        DB 操作重试适配器。

        fn: async (conv_mgr, event_repo, art_mgr) -> result
        有 db_manager 时委托 db_manager.with_retry（fresh session + 瞬断重试）。
        无 db_manager 时回退到 bound 实例（不重试）。
        """
        if not self._db_manager:
            return await fn(self.conversation_manager, self.message_event_repo, self.artifact_manager)

        from repositories.conversation_repo import ConversationRepository
        from repositories.message_event_repo import MessageEventRepository
        from repositories.artifact_repo import ArtifactRepository

        async def _with_session(session):
            conv_mgr = ConversationManager(ConversationRepository(session))
            event_repo = MessageEventRepository(session)
            art_mgr = ArtifactManager(ArtifactRepository(session))
            return await fn(conv_mgr, event_repo, art_mgr)

        return await self._db_manager.with_retry(_with_session)

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

        # 生成 ID
        message_id = message_id or f"msg-{uuid4().hex}"

        # 先发送元数据事件 — as early as possible so frontend knows we're alive
        # Only needs message_id, not a persisted row
        yield {
            "type": StreamEventType.METADATA.value,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "conversation_id": conversation_id,
                "message_id": message_id,
            }
        }

        # Wait for any running compaction before loading history
        # Yield COMPACTION_WAIT before blocking so frontend can show the indicator
        if self.compaction_manager and await self.compaction_manager.is_running(conversation_id):
            yield {
                "type": StreamEventType.COMPACTION_WAIT.value,
                "timestamp": datetime.now().isoformat(),
                "data": {"conversation_id": conversation_id, "status": "waiting"},
            }
            await self.compaction_manager.wait_if_running(conversation_id)

        # History (reads summaries written by compaction if we waited above)
        if parent_message_id is not _UNSET and resolved_parent is None:
            conversation_history = []
        else:
            conversation_history = await self._with_db_retry(
                lambda cm, er, am: cm.format_conversation_history_async(
                    conv_id=conversation_id, to_message_id=resolved_parent
                )
            )

        # Session
        session_id = conversation_id  # session_id = conversation_id

        # 设置 artifact session
        if self.artifact_manager:
            self.artifact_manager.set_session(session_id)

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

        # 添加消息到 conversation (after all pre-engine setup to avoid orphaned rows on failure)
        await self.conversation_manager.add_message_async(
            conv_id=conversation_id,
            message_id=message_id,
            user_input=user_input,
            parent_id=resolved_parent,
        )

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
                    hooks=self.hooks,
                    artifact_manager=self.artifact_manager,
                    emit=emit_to_queue,
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
        if self._on_engine_exit:
            await self._on_engine_exit(conversation_id, message_id)

        # ========== Post-processing ==========
        # Use initial_state as fallback if engine crashed before setting final_state
        if final_state is None:
            final_state = initial_state

        try:
            response = final_state.get("response", "")
            has_error = final_state.get("error", False)
            is_cancelled = final_state.get("cancelled", False)

            # Flush dirty artifacts to DB before updating response
            flush_error: Optional[str] = None
            if self.artifact_manager:
                try:
                    await self.artifact_manager.flush_all(
                        session_id, db_manager=self._db_manager
                    )
                except Exception as flush_err:
                    logger.exception(f"Artifact flush failed after retries: {flush_err}")
                    flush_error = f"Artifact persistence failed: {flush_err}"

            # 更新 conversation response
            final_response = response if not has_error else (response or "An error occurred during execution.")
            await self._with_db_retry(
                lambda cm, er, am: cm.update_response_async(
                    conv_id=conversation_id, message_id=message_id, response=final_response,
                )
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

            # 自动触发 compaction（cancelled 不触发）
            if self.compaction_manager and execution_metrics and not is_cancelled:
                await self.compaction_manager.maybe_trigger(
                    conv_id=conversation_id,
                    message_id=message_id,
                    execution_metrics=execution_metrics,
                )

            # 终态事件：error 路径由 engine 已发（在 state["events"] 中），
            # controller 负责 success 和 cancelled 路径的终态事件。
            terminal_event_dict = None
            if is_cancelled:
                terminal_event_dict = {
                    "type": StreamEventType.CANCELLED.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": False,
                        "cancelled": True,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "response": response,
                        "execution_metrics": final_state.get("execution_metrics", {}),
                    }
                }
            elif flush_error:
                terminal_event_dict = {
                    "type": StreamEventType.ERROR.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": False,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "error": flush_error,
                        "execution_metrics": final_state.get("execution_metrics", {}),
                    }
                }
            elif not has_error:
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

            if terminal_event_dict:
                # 终态事件入库
                final_state["events"].append(ExecutionEvent(
                    event_type=terminal_event_dict["type"],
                    agent_name=None,
                    data=terminal_event_dict["data"],
                ))

            # 持久化事件（batch write — 设计文档 §关键设计约束）
            await self._persist_events(message_id, final_state)

            # 发送终态到 SSE（error 已由 engine 实时推送）
            if terminal_event_dict:
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

        策略：fresh-session-per-attempt + retry_on_db_transient。
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

        try:
            await self._with_db_retry(
                lambda cm, er, am: er.batch_create(db_events)
            )
            logger.info(f"Persisted {len(db_events)} events for message {message_id}")
        except Exception as e:
            logger.error(
                f"Event persistence failed after retries for {message_id} "
                f"({len(db_events)} events lost): {e}"
            )


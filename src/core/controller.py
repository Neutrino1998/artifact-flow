"""
执行控制器（支持流式输出）
核心改进：
1. ConversationManager负责格式化对话历史
2. 复用ContextManager.compress_messages做智能裁剪
3. 支持流式输出 (stream_execute 方法)
4. 统一使用 StreamEventType 事件类型

注意：数据库事务管理由 API 层负责，Controller 不管理 session 生命周期。
API 层应通过依赖注入为每个请求创建独立的 Manager 实例。
"""

from typing import Dict, List, Optional, Any, AsyncGenerator
from uuid import uuid4
from datetime import datetime
from langgraph.types import Command

from core.state import create_initial_state
from core.context_manager import ContextManager
from core.conversation_manager import ConversationManager
from core.events import StreamEventType, finalize_metrics
from tools.implementations.artifact_ops import ArtifactManager
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ExecutionController:
    """
    执行控制器（支持流式输出）
    使用ConversationManager格式化对话历史
    提供批量和流式两种执行模式

    注意：数据库事务管理由 API 层负责。
    API 层应在请求开始时创建 session，并通过依赖注入传入已绑定 repository 的 Manager。
    """

    def __init__(
        self,
        compiled_graph,
        artifact_manager: Optional[ArtifactManager] = None,
        conversation_manager: Optional[ConversationManager] = None
    ):
        """
        初始化执行控制器

        Args:
            compiled_graph: 编译后的 LangGraph 图
            artifact_manager: Artifact 管理器（应已绑定 repository）
            conversation_manager: 对话管理器（应已绑定 repository）
        """
        self.graph = compiled_graph
        self.conversation_manager = conversation_manager or ConversationManager()
        self.artifact_manager = artifact_manager or ArtifactManager()

        logger.info("ExecutionController initialized")
    
    async def execute(
        self,
        content: Optional[str] = None,
        thread_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        message_id: Optional[str] = None,
        resume_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        批量执行接口

        场景1：新消息
            - 必需: content
            - 可选: conversation_id, parent_message_id

        场景2：恢复权限
            - 必需: thread_id, conversation_id, message_id, resume_data

        Args:
            content: 用户消息内容
            thread_id: 线程ID（恢复时使用）
            conversation_id: 对话ID
            parent_message_id: 父消息ID（分支时使用）
            message_id: 消息ID（恢复时使用，用于更新响应）
            resume_data: 恢复数据 {"type": "permission", "approved": bool}

        Returns:
            执行结果字典
        """
        # 场景1：新消息
        if content is not None:
            return await self._execute_new_message(
                content=content,
                conversation_id=conversation_id,
                parent_message_id=parent_message_id
            )
        # 场景2：恢复权限
        elif thread_id and resume_data and conversation_id and message_id:
            return await self._resume_from_permission(
                thread_id=thread_id,
                conversation_id=conversation_id,
                message_id=message_id,
                resume_data=resume_data
            )

        else:
            raise ValueError(
                "Either 'content' (for new message) or "
                "'thread_id + conversation_id + message_id + resume_data' (for resume) required"
            )
    
    async def stream_execute(
        self,
        content: Optional[str] = None,
        thread_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        message_id: Optional[str] = None,
        resume_data: Optional[Dict] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行接口

        场景1：新消息（流式）
            - 必需: content
            - 可选: conversation_id, parent_message_id

        场景2：恢复权限（流式）
            - 必需: thread_id, conversation_id, message_id, resume_data

        Args:
            content: 用户消息内容
            thread_id: 线程ID（恢复时使用）
            conversation_id: 对话ID
            parent_message_id: 父消息ID（分支时使用）
            message_id: 消息ID（恢复时使用，用于更新响应）
            resume_data: 恢复数据 {"type": "permission", "approved": bool}

        Yields:
            流式事件字典:
            {
                "event_type": "stream" | "metadata" | "complete",
                "data": {...}
            }
        """
        # 场景1：新消息
        if content is not None:
            async for event in self._stream_new_message(
                content=content,
                conversation_id=conversation_id,
                parent_message_id=parent_message_id
            ):
                yield event

        # 场景2：恢复权限
        elif thread_id and resume_data and conversation_id and message_id:
            async for event in self._stream_resume_from_permission(
                thread_id=thread_id,
                conversation_id=conversation_id,
                message_id=message_id,
                resume_data=resume_data
            ):
                yield event

        else:
            raise ValueError(
                "Either 'content' (for new message) or "
                "'thread_id + conversation_id + message_id + resume_data' (for resume) required"
            )
    
    async def _execute_new_message(
        self,
        content: str,
        conversation_id: Optional[str],
        parent_message_id: Optional[str]
    ) -> Dict[str, Any]:
        """
        处理新消息（批量模式）

        流程：
        1. 确保conversation存在
        2. 自动设置父消息ID（如果未指定）
        3. 获取对话历史
        4. 格式化对话历史
        5. 创建初始状态（包含对话历史）
        6. 添加消息到conversation
        7. 执行graph
        8. 处理结果（中断或完成）
        """

        # 1. 确保conversation存在（使用异步方法以支持持久化）
        if not conversation_id:
            conversation_id = await self.conversation_manager.start_conversation_async()
        else:
            await self.conversation_manager.ensure_conversation_exists(conversation_id)
        
        # 2. 自动设置父消息ID（如果未指定）
        if not parent_message_id:
            parent_message_id = await self.conversation_manager.get_active_branch(conversation_id)
            if parent_message_id:
                logger.debug(f"Auto-set parent_message_id to current active_branch: {parent_message_id}")

        # 3. 格式化对话历史（使用ConversationManager的方法）
        conversation_history = await self.conversation_manager.format_conversation_history_async(
            conv_id=conversation_id,
            to_message_id=parent_message_id
        )
        
        # 4. 生成ID
        message_id = f"msg-{uuid4().hex}"
        thread_id = f"thd-{uuid4().hex}"
        
        # 5. 获取session
        session_id = self._get_or_create_session(conversation_id)
        # 5.5. 设置 artifact session 并清除上一轮的临时 artifacts
        if self.artifact_manager:
            self.artifact_manager.set_session(session_id)
            try:
                await self.artifact_manager.clear_temporary_artifacts(session_id)
            except Exception as e:
                logger.warning(f"Failed to clear temporary artifacts: {e}")
        
        # 6. 创建初始状态
        initial_state = create_initial_state(
            task=content,
            session_id=session_id,
            thread_id=thread_id,
            message_id=message_id,
            conversation_history=conversation_history
        )
        
        logger.info(f"Processing new message in conversation {conversation_id}")
        
        # 7. 添加消息到conversation
        await self.conversation_manager.add_message_async(
            conv_id=conversation_id,
            message_id=message_id,
            content=content,
            thread_id=thread_id,
            parent_id=parent_message_id
        )
        
        # 8. 执行graph
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 100  # 工具循环在 graph 层，需要更高限制
        }
        
        try:
            result = await self.graph.ainvoke(initial_state, config)
            
            # 8. 处理结果
            if result.get("__interrupt__"):
                # 权限中断: __interrupt__ 是一个列表，包含 Interrupt 对象
                interrupts = result["__interrupt__"]

                # 取第一个 Interrupt 对象的 value 属性
                interrupt_data = interrupts[0].value

                logger.info(f"⚠️ Execution interrupted: {interrupt_data['type']}")

                # 返回中断信息，前端需保存 conversation_id, message_id, thread_id 用于后续 resume
                return {
                    "success": True,
                    "interrupted": True,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "interrupt_type": interrupt_data["type"],
                    "interrupt_data": interrupt_data
                }
            
            else:
                # 正常完成
                response = result.get("graph_response", "")

                # 更新conversation response
                await self.conversation_manager.update_response_async(
                    conv_id=conversation_id,
                    message_id=message_id,
                    response=response
                )

                logger.info(f"✅ Execution completed")

                return {
                    "success": True,
                    "interrupted": False,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "response": response
                }

        except Exception as e:
            logger.exception(f"Error in graph execution: {e}")

            # 更新错误响应
            error_msg = f"Error: {str(e)}"
            await self.conversation_manager.update_response_async(
                conv_id=conversation_id,
                message_id=message_id,
                response=error_msg
            )
            
            return {
                "success": False,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "thread_id": thread_id,
                "error": str(e)
            }
    
    async def _stream_new_message(
        self,
        content: str,
        conversation_id: Optional[str],
        parent_message_id: Optional[str]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        🆕 处理新消息（流式模式）
        
        流程：
        1-6. 准备工作（与批量模式相同）
        7. 使用 graph.astream() 流式执行
        8. 实时yield事件
        """
        
        # 1-6. 准备工作（与批量模式相同）
        if not conversation_id:
            conversation_id = await self.conversation_manager.start_conversation_async()
        else:
            await self.conversation_manager.ensure_conversation_exists(conversation_id)

        if not parent_message_id:
            parent_message_id = await self.conversation_manager.get_active_branch(conversation_id)
            if parent_message_id:
                logger.debug(f"Auto-set parent_message_id to current active_branch: {parent_message_id}")

        conversation_history = await self.conversation_manager.format_conversation_history_async(
            conv_id=conversation_id,
            to_message_id=parent_message_id
        )
        
        message_id = f"msg-{uuid4().hex}"
        thread_id = f"thd-{uuid4().hex}"
        
        session_id = self._get_or_create_session(conversation_id)
        if self.artifact_manager:
            self.artifact_manager.set_session(session_id)
            try:
                await self.artifact_manager.clear_temporary_artifacts(session_id)
            except Exception as e:
                logger.warning(f"Failed to clear temporary artifacts: {e}")
        
        initial_state = create_initial_state(
            task=content,
            session_id=session_id,
            thread_id=thread_id,
            message_id=message_id,
            conversation_history=conversation_history
        )
        
        logger.info(f"Processing new message (streaming) in conversation {conversation_id}")

        await self.conversation_manager.add_message_async(
            conv_id=conversation_id,
            message_id=message_id,
            content=content,
            thread_id=thread_id,
            parent_id=parent_message_id
        )
        
        # 先发送元数据事件
        yield {
            "type": StreamEventType.METADATA.value,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "thread_id": thread_id
            }
        }

        # 7-8. 流式执行graph
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 100  # 工具循环在 graph 层，需要更高限制
        }

        try:
            # 使用 astream() 替代 ainvoke()，并指定 stream_mode="custom"
            final_response = None

            async for chunk in self.graph.astream(
                initial_state,
                config,
                stream_mode="custom"  # 关键：使用 custom 模式
            ):
                # chunk 是一个字典，包含我们在 graph 中通过 writer() 发送的数据
                # 格式: {"type": "...", "agent": "...", "timestamp": "...", "data": {...}}

                # 直接透传事件（已经是统一的 StreamEventType 格式）
                yield chunk

                # 收集最终响应（从 AGENT_COMPLETE 事件）
                if chunk.get("type") == StreamEventType.AGENT_COMPLETE.value and chunk.get("data"):
                    final_response = chunk["data"].get("content", "")

            # 检查是否有中断
            final_state = await self.graph.aget_state(config)

            # 完成 execution_metrics
            execution_metrics = final_state.values.get("execution_metrics", {})
            finalize_metrics(execution_metrics)

            # 注意：在流式模式下，中断的检测方式不同。应检查 final_state.interrupts 而不是 values["__interrupt__"]
            if final_state.interrupts:
                # 权限中断
                interrupt_data = final_state.interrupts[0].value

                logger.info(f"⚠️ Execution interrupted: {interrupt_data['type']}")

                # 发送中断事件，前端需保存 conversation_id, message_id, thread_id 用于后续 resume
                yield {
                    "type": StreamEventType.COMPLETE.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": True,
                        "interrupted": True,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "interrupt_type": interrupt_data["type"],
                        "interrupt_data": interrupt_data,
                        "execution_metrics": execution_metrics
                    }
                }

            else:
                # 正常完成
                response = final_state.values.get("graph_response", final_response or "")

                await self.conversation_manager.update_response_async(
                    conv_id=conversation_id,
                    message_id=message_id,
                    response=response
                )

                logger.info(f"✅ Streaming execution completed")

                # 发送完成事件（包含 execution_metrics）
                yield {
                    "type": StreamEventType.COMPLETE.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": True,
                        "interrupted": False,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "response": response,
                        "execution_metrics": execution_metrics
                    }
                }

        except Exception as e:
            logger.exception(f"Error in streaming graph execution: {e}")

            error_msg = f"Error: {str(e)}"
            await self.conversation_manager.update_response_async(
                conv_id=conversation_id,
                message_id=message_id,
                response=error_msg
            )

            # 发送错误事件
            yield {
                "type": StreamEventType.ERROR.value,
                "timestamp": datetime.now().isoformat(),
                "data": {
                    "success": False,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "error": str(e)
                }
            }
    
    async def _resume_from_permission(
        self,
        thread_id: str,
        conversation_id: str,
        message_id: str,
        resume_data: Dict
    ) -> Dict[str, Any]:
        """
        从权限中断恢复（批量模式）

        Args:
            thread_id: 线程ID
            conversation_id: 对话ID
            message_id: 消息ID（用于更新响应）
            resume_data: 恢复数据 {"type": "permission", "approved": bool}

        Returns:
            执行结果
        """

        logger.info(f"Resuming thread {thread_id} after permission")

        # 恢复 artifact session（session_id 与 conversation_id 相同）
        if self.artifact_manager:
            self.artifact_manager.set_session(conversation_id)

        # 恢复执行
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 100
        }

        try:
            result = await self.graph.ainvoke(
                Command(resume=resume_data.get("approved", False)),
                config
            )

            # 更新conversation response
            response = result.get("graph_response", "")
            await self.conversation_manager.update_response_async(
                conv_id=conversation_id,
                message_id=message_id,
                response=response
            )

            logger.info(f"✅ Resumed execution completed")

            return {
                "success": True,
                "interrupted": False,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "thread_id": thread_id,
                "response": response
            }

        except Exception as e:
            logger.exception(f"Error in resume execution: {e}")

            return {
                "success": False,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "thread_id": thread_id,
                "error": str(e)
            }
    
    async def _stream_resume_from_permission(
        self,
        thread_id: str,
        conversation_id: str,
        message_id: str,
        resume_data: Dict
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        从权限中断恢复（流式模式）

        Args:
            thread_id: 线程ID
            conversation_id: 对话ID
            message_id: 消息ID（用于更新响应）
            resume_data: 恢复数据 {"type": "permission", "approved": bool}

        Yields:
            流式事件
        """

        logger.info(f"Resuming thread {thread_id} after permission (streaming)")

        # 恢复 artifact session（session_id 与 conversation_id 相同）
        if self.artifact_manager:
            self.artifact_manager.set_session(conversation_id)

        # 发送元数据
        yield {
            "type": StreamEventType.METADATA.value,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "thread_id": thread_id,
                "resuming": True
            }
        }

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 100
        }

        try:
            final_response = None

            async for chunk in self.graph.astream(
                Command(resume=resume_data.get("approved", False)),
                config,
                stream_mode="custom"
            ):
                # 直接透传事件
                yield chunk

                if chunk.get("type") == StreamEventType.AGENT_COMPLETE.value and chunk.get("data"):
                    final_response = chunk["data"].get("content", "")

            # 获取最终状态
            final_state = await self.graph.aget_state(config)

            # 完成 execution_metrics
            execution_metrics = final_state.values.get("execution_metrics", {})
            finalize_metrics(execution_metrics)

            response = final_state.values.get("graph_response", final_response or "")

            await self.conversation_manager.update_response_async(
                conv_id=conversation_id,
                message_id=message_id,
                response=response
            )

            logger.info(f"✅ Streaming resumed execution completed")

            yield {
                "type": StreamEventType.COMPLETE.value,
                "timestamp": datetime.now().isoformat(),
                "data": {
                    "success": True,
                    "interrupted": False,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "response": response,
                    "execution_metrics": execution_metrics
                }
            }

        except Exception as e:
            logger.exception(f"Error in streaming resume execution: {e}")

            yield {
                "type": StreamEventType.ERROR.value,
                "timestamp": datetime.now().isoformat(),
                "data": {
                    "success": False,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "error": str(e)
                }
            }
    
    def _get_or_create_session(self, conversation_id: str) -> str:
        """
        为conversation获取或创建artifact session ID
        一个conversation对应一个artifact session

        注意：session_id 与 conversation_id 相同，这是因为 ArtifactSession.id
        是 Conversation.id 的外键
        """
        return conversation_id
    
    async def get_conversation_history(self, conversation_id: str) -> List[Dict]:
        """获取对话历史（用于展示）"""
        return await self.conversation_manager.get_conversation_path_async(conversation_id)

    async def list_conversations(self) -> List[Dict]:
        """列出所有对话"""
        return await self.conversation_manager.list_conversations_async()
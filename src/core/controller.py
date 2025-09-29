"""
执行控制器和对话管理器
支持分支对话和interrupt恢复
"""

from typing import Dict, List, Optional, Any
from uuid import uuid4
from datetime import datetime
from langgraph.types import Command

from core.state import (
    AgentState, UserMessage, ConversationTree, 
    create_initial_state
)
from utils.logger import get_logger

logger = get_logger("Core")


class ConversationManager:
    """
    用户对话管理器（Layer 1）
    管理对话树和分支
    """
    
    def __init__(self):
        """初始化对话管理器"""
        self.conversations: Dict[str, ConversationTree] = {}
        logger.info("ConversationManager initialized")
    
    def start_conversation(self, conversation_id: Optional[str] = None) -> str:
        """
        开始新对话
        
        Args:
            conversation_id: 指定的对话ID（可选）
            
        Returns:
            对话ID
        """
        conv_id = conversation_id or f"conv-{uuid4()}"
        
        self.conversations[conv_id] = {
            "conversation_id": conv_id,
            "branches": {},
            "messages": {},
            "active_branch": "",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        logger.info(f"Started new conversation: {conv_id}")
        return conv_id
    
    def add_message(
        self,
        conv_id: str,
        message_id: str,
        content: str,
        thread_id: str,
        parent_id: Optional[str] = None,
        graph_response: Optional[str] = None
    ) -> UserMessage:
        """
        添加消息到对话树
        
        Args:
            conv_id: 对话ID
            message_id: 消息ID
            content: 消息内容
            thread_id: 关联的线程ID
            parent_id: 父消息ID
            graph_response: Graph响应
            
        Returns:
            用户消息对象
        """
        if conv_id not in self.conversations:
            raise ValueError(f"Conversation {conv_id} not found")
        
        conversation = self.conversations[conv_id]
        
        # 创建消息
        user_msg: UserMessage = {
            "message_id": message_id,
            "parent_id": parent_id,
            "content": content,
            "thread_id": thread_id,
            "timestamp": datetime.now().isoformat(),
            "graph_response": graph_response,
            "metadata": {}
        }
        
        # 保存消息
        conversation["messages"][message_id] = user_msg
        
        # 更新分支关系
        if parent_id:
            if parent_id not in conversation["branches"]:
                conversation["branches"][parent_id] = []
            conversation["branches"][parent_id].append(message_id)
            
            # 检查是否创建了新分支
            if len(conversation["branches"][parent_id]) > 1:
                logger.info(f"🌿 Created new branch from message {parent_id}")
        
        # 更新活跃分支
        conversation["active_branch"] = message_id
        conversation["updated_at"] = datetime.now().isoformat()
        
        return user_msg
    
    def update_response(
        self, 
        conv_id: str, 
        message_id: str, 
        response: str
    ) -> None:
        """
        更新消息的Graph响应
        
        Args:
            conv_id: 对话ID
            message_id: 消息ID
            response: Graph响应
        """
        if conv_id in self.conversations:
            if message_id in self.conversations[conv_id]["messages"]:
                self.conversations[conv_id]["messages"][message_id]["graph_response"] = response
                self.conversations[conv_id]["updated_at"] = datetime.now().isoformat()
    
    def get_conversation_path(
        self, 
        conv_id: str,
        to_message_id: Optional[str] = None
    ) -> List[UserMessage]:
        """
        获取对话路径（从根到指定消息）
        
        Args:
            conv_id: 对话ID
            to_message_id: 目标消息ID（None则使用活跃分支）
            
        Returns:
            消息路径列表
        """
        if conv_id not in self.conversations:
            return []
        
        conversation = self.conversations[conv_id]
        target_id = to_message_id or conversation.get("active_branch")
        
        if not target_id or target_id not in conversation["messages"]:
            return []
        
        # 向上追溯到根
        path = []
        current = conversation["messages"][target_id]
        
        while current:
            path.insert(0, current)
            if current["parent_id"] and current["parent_id"] in conversation["messages"]:
                current = conversation["messages"][current["parent_id"]]
            else:
                break
        
        return path


class ExecutionController:
    """
    执行控制器
    管理Graph执行和interrupt恢复
    """
    
    def __init__(self, compiled_graph):
        """
        初始化控制器
        
        Args:
            compiled_graph: 编译后的LangGraph
        """
        self.graph = compiled_graph
        self.conversation_manager = ConversationManager()
        
        # 线程状态缓存（用于分支）
        self.thread_states: Dict[str, Dict] = {}
        
        # 保存中断的线程信息
        self.interrupted_threads: Dict[str, Dict] = {}
        
        logger.info("ExecutionController initialized")
    
    async def execute(
        self,
        # 核心参数
        content: Optional[str] = None,           # 新消息内容（新对话时必需）
        thread_id: Optional[str] = None,         # 线程ID（恢复时必需）
        
        # 对话管理
        conversation_id: Optional[str] = None,   # 对话ID
        parent_message_id: Optional[str] = None, # 父消息ID（用于分支）
        session_id: Optional[str] = None,        # Artifact会话ID
        
        # 恢复执行参数
        resume_data: Optional[Dict] = None,      # 恢复数据
        # resume_data = {
        #     "approved": bool,                   # 是否批准（权限确认）
        #     "reason": str,                       # 拒绝原因（可选）
        #     "type": "permission" | "custom"     # 恢复类型
        # }
    ) -> Dict[str, Any]:
        """
        统一的执行接口
        
        支持三种场景：
        1. 新对话: content必需
        2. 恢复权限: thread_id + resume_data必需
        3. 分支对话: content + parent_message_id必需
        
        Returns:
            执行结果字典
        """
        # ========== 1. 参数验证和场景识别 ==========
        is_new_message = content is not None
        is_resuming = thread_id is not None and resume_data is not None
        
        if not is_new_message and not is_resuming:
            raise ValueError("Either 'content' (new message) or 'thread_id' + 'resume_data' (resume) required")
        
        if is_new_message and is_resuming:
            raise ValueError("Cannot specify both new message and resume parameters")
        
        # ========== 2. 处理新消息场景 ==========
        if is_new_message:
            return await self._execute_new_message(
                content=content,
                conversation_id=conversation_id,
                parent_message_id=parent_message_id,
                session_id=session_id
            )
        
        # ========== 3. 处理恢复场景 ==========
        else:  # is_resuming
            return await self._resume_execution(
                thread_id=thread_id,
                resume_data=resume_data
            )
    
    async def _execute_new_message(
        self,
        content: str,
        conversation_id: Optional[str],
        parent_message_id: Optional[str],
        session_id: Optional[str]
    ) -> Dict[str, Any]:
        """
        执行新消息（内部方法）
        """
        # 确保对话存在
        if not conversation_id:
            conversation_id = self.conversation_manager.start_conversation()
        elif conversation_id not in self.conversation_manager.conversations:
            self.conversation_manager.start_conversation(conversation_id)
        
        # 生成ID
        message_id = f"msg-{uuid4()}"
        thread_id = f"thd-{uuid4()}"
        
        # 获取或创建session
        if not session_id:
            from tools.implementations.artifact_ops import _artifact_store
            session_id = _artifact_store.current_session_id or _artifact_store.create_session()
        
        # 创建初始状态
        parent_thread_id = None
        if parent_message_id:
            parent_msg = self.conversation_manager.conversations.get(
                conversation_id, {}
            ).get("messages", {}).get(parent_message_id)
            if parent_msg:
                parent_thread_id = parent_msg.get("thread_id")
        
        initial_state = create_initial_state(
            task=content,
            session_id=session_id,
            thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            message_id=message_id
        )

        # 添加消息到对话树
        self.conversation_manager.add_message(
            conv_id=conversation_id,
            message_id=message_id,
            content=content,
            thread_id=thread_id,
            parent_id=parent_message_id
        )
        
        # 执行Graph
        config = {"configurable": {"thread_id": thread_id}}
        
        # 保存执行上下文（用于可能的恢复）
        execution_context = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "thread_id": thread_id,
            "session_id": session_id
        }
        
        return await self._execute_graph(
            input_data=initial_state,
            config=config,
            execution_context=execution_context
        )
    
    async def _resume_execution(
        self,
        thread_id: str,
        resume_data: Dict
    ) -> Dict[str, Any]:
        """
        恢复执行（内部方法）
        """
        # 检查中断信息
        if thread_id not in self.interrupted_threads:
            raise ValueError(f"No interrupted execution for thread {thread_id}")
        
        interrupt_info = self.interrupted_threads[thread_id]
        config = {"configurable": {"thread_id": thread_id}}
        
        # 准备恢复命令
        if resume_data.get("type") == "permission":
            # 权限恢复：使用Command
            from langgraph.types import Command
            input_data = Command(resume=resume_data.get("approved", False))
        else:
            # 其他类型的恢复（扩展点）
            input_data = resume_data.get("data", {})
        
        logger.info(f"Resuming thread {thread_id} with type: {resume_data.get('type')}")
        
        # 恢复上下文
        execution_context = {
            "conversation_id": interrupt_info["conversation_id"],
            "message_id": interrupt_info["message_id"],
            "thread_id": thread_id,
            "session_id": interrupt_info.get("session_id")
        }
        
        return await self._execute_graph(
            input_data=input_data,
            config=config,
            execution_context=execution_context,
            is_resume=True
        )
    
    async def _execute_graph(
        self,
        input_data: Any,
        config: Dict,
        execution_context: Dict,
        is_resume: bool = False
    ) -> Dict[str, Any]:
        """
        核心Graph执行逻辑（共享）
        
        Args:
            input_data: 输入数据（初始状态或Command）
            config: LangGraph配置
            execution_context: 执行上下文
            is_resume: 是否是恢复执行
            
        Returns:
            统一格式的执行结果
        """
        try:
            logger.info(f"{'Resuming' if is_resume else 'Starting'} graph execution for thread {execution_context['thread_id'][:8]}...")
            
            # 执行Graph
            result = await self.graph.ainvoke(input_data, config)
            
            # ========== 处理中断 ==========
            if isinstance(result, dict) and result.get("__interrupt__"):
                interrupt_data = result.get("__interrupt__")
                
                # 保存中断信息
                self.interrupted_threads[execution_context["thread_id"]] = {
                    **execution_context,
                    "interrupt_data": interrupt_data,
                    "timestamp": datetime.now().isoformat()
                }
                
                logger.info(f"Execution interrupted: {interrupt_data.get('type')}")
                
                return {
                    "success": True,
                    "interrupted": True,
                    **execution_context,
                    "interrupt_type": interrupt_data.get("type"),
                    "interrupt_data": interrupt_data
                }
            
            # ========== 正常完成 ==========
            final_state = result
            
            # 保存线程状态
            self.thread_states[execution_context["thread_id"]] = final_state
            
            # 清除中断信息（如果是恢复执行）
            if is_resume and execution_context["thread_id"] in self.interrupted_threads:
                del self.interrupted_threads[execution_context["thread_id"]]
            
            # 获取响应
            response = final_state.get("graph_response", "")
            
            # 更新对话响应
            self.conversation_manager.update_response(
                execution_context["conversation_id"],
                execution_context["message_id"],
                response
            )
            
            return {
                "success": True,
                "interrupted": False,
                **execution_context,
                "response": response,
            }
            
        except Exception as e:
            logger.exception(f"Error in graph execution: {e}")
            
            # 更新错误响应
            error_msg = f"Error: {str(e)}"
            if execution_context.get("conversation_id") and execution_context.get("message_id"):
                self.conversation_manager.update_response(
                    execution_context["conversation_id"],
                    execution_context["message_id"],
                    error_msg
                )
            
            return {
                "success": False,
                **execution_context,
                "error": str(e)
            }
    
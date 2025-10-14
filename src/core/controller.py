"""
执行控制器
核心改进：
1. ConversationManager负责格式化对话历史
2. 复用ContextManager.compress_messages做智能裁剪
"""

from typing import Dict, List, Optional, Any
from uuid import uuid4
from datetime import datetime
from langgraph.types import Command

from core.state import create_initial_state
from core.context_manager import ContextManager
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ConversationManager:
    """
    对话管理器
    职责：
    1. 维护用户的对话树
    2. 格式化对话历史为可读文本
    """
    
    def __init__(self):
        self.conversations: Dict[str, Dict] = {}
        logger.info("ConversationManager initialized")
    
    def start_conversation(self, conversation_id: Optional[str] = None) -> str:
        """
        开始新对话
        
        Args:
            conversation_id: 指定的对话ID
            
        Returns:
            对话ID
        """
        conv_id = conversation_id or f"conv-{uuid4().hex}"
        
        self.conversations[conv_id] = {
            "conversation_id": conv_id,
            "branches": {},  # parent_id -> [child_ids]
            "messages": {},  # message_id -> UserMessage
            "active_branch": "",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        logger.info(f"Started conversation: {conv_id}")
        return conv_id
    
    def add_message(
        self,
        conv_id: str,
        message_id: str,
        content: str,
        thread_id: str,
        parent_id: Optional[str] = None
    ) -> Dict:
        """
        添加消息到对话树
        
        Args:
            conv_id: 对话ID
            message_id: 消息ID
            content: 消息内容
            thread_id: 关联的Graph线程ID
            parent_id: 父消息ID（分支时使用）
            
        Returns:
            用户消息对象
        """
        if conv_id not in self.conversations:
            raise ValueError(f"Conversation {conv_id} not found")
        
        conversation = self.conversations[conv_id]
        
        # 创建消息
        user_msg = {
            "message_id": message_id,
            "parent_id": parent_id,
            "content": content,
            "thread_id": thread_id,
            "timestamp": datetime.now().isoformat(),
            "graph_response": None,
            "metadata": {}
        }
        
        # 保存消息
        conversation["messages"][message_id] = user_msg
        
        # 更新分支关系
        if parent_id:
            if parent_id not in conversation["branches"]:
                conversation["branches"][parent_id] = []
            conversation["branches"][parent_id].append(message_id)
            
            if len(conversation["branches"][parent_id]) > 1:
                logger.info(f"🌿 Created branch from message {parent_id}")
        
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
        """更新消息的Graph响应"""
        if conv_id in self.conversations:
            if message_id in self.conversations[conv_id]["messages"]:
                self.conversations[conv_id]["messages"][message_id]["graph_response"] = response
                self.conversations[conv_id]["updated_at"] = datetime.now().isoformat()
    
    def get_conversation_path(
        self,
        conv_id: str,
        to_message_id: Optional[str] = None
    ) -> List[Dict]:
        """
        获取对话路径（从根到指定消息）
        
        Args:
            conv_id: 对话ID
            to_message_id: 目标消息ID（None则使用活跃分支）
            
        Returns:
            消息路径列表（UserMessage对象）
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
    
    def format_conversation_history(
        self,
        conv_id: str,
        to_message_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        格式化对话历史为消息列表

        Args:
            conv_id: 对话ID
            to_message_id: 目标消息ID（None则使用活跃分支）
        
        Returns:
            消息列表 [{"role": "user", "content": ...}, {"role": "assistant", ...}, ...]
        """
        # 1. 获取对话路径
        conversation_path = self.get_conversation_path(conv_id, to_message_id)
        
        if not conversation_path:
            return []
        
        # 2. 转换为标准消息格式
        messages = []
        for msg in conversation_path:
            messages.append({
                "role": "user",
                "content": msg["content"]
            })
            
            if msg.get("graph_response"):
                messages.append({
                    "role": "assistant",
                    "content": msg["graph_response"]
                })
        
        logger.debug(f"Formatted {len(messages)} messages from conversation history")
        return messages


class ExecutionController:
    """
    执行控制器
    使用ConversationManager格式化对话历史
    """
    
    def __init__(self, compiled_graph):
        self.graph = compiled_graph
        self.conversation_manager = ConversationManager()
        
        # 只为permission保存中断信息
        self.interrupted_threads: Dict[str, Dict] = {}
        
        logger.info("ExecutionController initialized")
    
    async def execute(
        self,
        content: Optional[str] = None,
        thread_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        resume_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        统一执行接口
        
        场景1：新消息
            - 必需: content
            - 可选: conversation_id, parent_message_id
            
        场景2：恢复权限
            - 必需: thread_id, resume_data
        
        Args:
            content: 用户消息内容
            thread_id: 线程ID（恢复时使用）
            conversation_id: 对话ID
            parent_message_id: 父消息ID（分支时使用）
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
        elif thread_id and resume_data:
            return await self._resume_from_permission(
                thread_id=thread_id,
                resume_data=resume_data
            )
        
        else:
            raise ValueError("Either 'content' or 'thread_id + resume_data' required")
    
    async def _execute_new_message(
        self,
        content: str,
        conversation_id: Optional[str],
        parent_message_id: Optional[str]
    ) -> Dict[str, Any]:
        """
        处理新消息
        
        流程：
        1. 确保conversation存在
        2. 获取对话历史
        3. 格式化对话历史
        4. 创建初始状态（包含对话历史）
        5. 添加消息到conversation
        6. 执行graph
        7. 处理结果（中断或完成）
        """
        
        # 1. 确保conversation存在
        if not conversation_id:
            conversation_id = self.conversation_manager.start_conversation()
        elif conversation_id not in self.conversation_manager.conversations:
            self.conversation_manager.start_conversation(conversation_id)
        
        # 2. 格式化对话历史（使用ConversationManager的方法）
        conversation_history = self.conversation_manager.format_conversation_history(
            conv_id=conversation_id,
            to_message_id=parent_message_id
        )
        
        # 3. 生成ID
        message_id = f"msg-{uuid4().hex}"
        thread_id = f"thd-{uuid4().hex}"
        
        # 4. 获取session
        session_id = self._get_or_create_session(conversation_id)
        
        # 5. 创建初始状态
        initial_state = create_initial_state(
            task=content,
            session_id=session_id,
            thread_id=thread_id,
            message_id=message_id,
            conversation_history=conversation_history
        )
        
        logger.info(f"Processing new message in conversation {conversation_id}")
        if conversation_history:
            # 计算实际的消息对数
            path = self.conversation_manager.get_conversation_path(
                conversation_id, parent_message_id
            )
            logger.debug(f"With conversation history: {len(path)} messages in path")
        
        # 6. 添加消息到conversation
        self.conversation_manager.add_message(
            conv_id=conversation_id,
            message_id=message_id,
            content=content,
            thread_id=thread_id,
            parent_id=parent_message_id
        )
        
        # 7. 执行graph
        config = {"configurable": {"thread_id": thread_id}}
        
        try:
            result = await self.graph.ainvoke(initial_state, config)
            
            # 8. 处理结果
            if result.get("__interrupt__"):
                # 权限中断
                interrupt_data = result["__interrupt__"]
                
                # 保存中断信息
                self.interrupted_threads[thread_id] = {
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "interrupt_data": interrupt_data,
                    "timestamp": datetime.now().isoformat()
                }
                
                logger.info(f"⚠️ Execution interrupted: {interrupt_data['type']}")
                
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
                self.conversation_manager.update_response(
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
            self.conversation_manager.update_response(
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
    
    async def _resume_from_permission(
        self,
        thread_id: str,
        resume_data: Dict
    ) -> Dict[str, Any]:
        """
        从权限中断恢复
        
        Args:
            thread_id: 线程ID
            resume_data: 恢复数据 {"type": "permission", "approved": bool}
            
        Returns:
            执行结果
        """
        
        # 1. 检查中断信息
        if thread_id not in self.interrupted_threads:
            raise ValueError(f"No interrupted execution for thread {thread_id}")
        
        interrupt_info = self.interrupted_threads[thread_id]
        
        logger.info(f"Resuming thread {thread_id} after permission")
        
        # 2. 恢复执行
        config = {"configurable": {"thread_id": thread_id}}
        
        try:
            result = await self.graph.ainvoke(
                Command(resume=resume_data.get("approved", False)),
                config
            )
            
            # 3. 清理中断信息
            del self.interrupted_threads[thread_id]
            
            # 4. 更新conversation response
            response = result.get("graph_response", "")
            self.conversation_manager.update_response(
                conv_id=interrupt_info["conversation_id"],
                message_id=interrupt_info["message_id"],
                response=response
            )
            
            logger.info(f"✅ Resumed execution completed")
            
            return {
                "success": True,
                "interrupted": False,
                "conversation_id": interrupt_info["conversation_id"],
                "message_id": interrupt_info["message_id"],
                "thread_id": thread_id,
                "response": response
            }
        
        except Exception as e:
            logger.exception(f"Error in resume execution: {e}")
            
            return {
                "success": False,
                "conversation_id": interrupt_info["conversation_id"],
                "message_id": interrupt_info["message_id"],
                "thread_id": thread_id,
                "error": str(e)
            }
    
    def _get_or_create_session(self, conversation_id: str) -> str:
        """
        为conversation获取或创建artifact session
        一个conversation对应一个artifact session
        """
        from tools.implementations.artifact_ops import _artifact_store
        
        session_id = f"sess-{conversation_id}"
        if session_id not in _artifact_store.sessions:
            _artifact_store.create_session(session_id)
        
        return session_id
    
    def get_conversation_history(self, conversation_id: str) -> List[Dict]:
        """获取对话历史（用于展示）"""
        return self.conversation_manager.get_conversation_path(conversation_id)
    
    def list_conversations(self) -> List[Dict]:
        """列出所有对话"""
        conversations = []
        for conv_id, conv in self.conversation_manager.conversations.items():
            conversations.append({
                "conversation_id": conv_id,
                "message_count": len(conv["messages"]),
                "branch_count": len(conv["branches"]),
                "created_at": conv["created_at"],
                "updated_at": conv["updated_at"]
            })
        return conversations
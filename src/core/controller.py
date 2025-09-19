"""
执行控制器和对话管理器
支持分支对话和权限处理
"""

from typing import Dict, List, Optional, Any, Tuple
from uuid import uuid4
from datetime import datetime

from core.state import (
    AgentState, UserMessage, ConversationTree, 
    create_initial_state
)
from tools.base import ToolResult
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
        conv_id = conversation_id or str(uuid4())
        
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
    
    def get_branches(self, conv_id: str, from_message_id: str) -> List[str]:
        """
        获取某个消息的所有分支
        
        Args:
            conv_id: 对话ID
            from_message_id: 消息ID
            
        Returns:
            子消息ID列表
        """
        if conv_id not in self.conversations:
            return []
        
        return self.conversations[conv_id]["branches"].get(from_message_id, [])


class ExecutionController:
    """
    执行控制器
    管理Graph执行和权限处理
    """
    
    def __init__(self, compiled_graph):
        """
        初始化控制器
        
        Args:
            compiled_graph: 编译后的LangGraph
        """
        self.graph = compiled_graph
        self.conversation_manager = ConversationManager()
        
        # 线程状态缓存（用于权限恢复）
        self.thread_states: Dict[str, Dict] = {}
        
        logger.info("ExecutionController initialized")
    
    async def process_message(
        self,
        content: str,
        conversation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理用户消息（主入口）
        
        Args:
            content: 用户消息内容
            conversation_id: 对话ID（None则创建新对话）
            parent_message_id: 父消息ID（用于分支）
            session_id: Artifact会话ID
            
        Returns:
            包含响应的字典
        """
        # 确保对话存在
        if not conversation_id:
            conversation_id = self.conversation_manager.start_conversation()
        elif conversation_id not in self.conversation_manager.conversations:
            self.conversation_manager.start_conversation(conversation_id)
        
        # 生成ID
        message_id = str(uuid4())
        thread_id = str(uuid4())
        
        # 如果从Artifact store获取session
        if not session_id:
            from tools.implementations.artifact_ops import _artifact_store
            session_id = _artifact_store.current_session_id or _artifact_store.create_session()
        
        # 创建初始状态
        parent_thread_id = None
        if parent_message_id:
            # 获取父消息的thread_id
            parent_msg = self.conversation_manager.conversations.get(
                conversation_id, {}
            ).get("messages", {}).get(parent_message_id)
            if parent_msg:
                parent_thread_id = parent_msg.get("thread_id")
        
        initial_state = create_initial_state(
            task=content,
            session_id=session_id,
            thread_id=thread_id,
            parent_thread_id=parent_thread_id
        )
        initial_state["user_message_id"] = message_id
        
        # 如果有父线程，尝试继承一些状态
        if parent_thread_id and parent_thread_id in self.thread_states:
            parent_state = self.thread_states[parent_thread_id]
            # 继承artifacts
            initial_state["task_plan_id"] = parent_state.get("task_plan_id")
            initial_state["result_artifact_ids"] = parent_state.get("result_artifact_ids", []).copy()
        
        # 添加消息到对话树（先不加response）
        self.conversation_manager.add_message(
            conv_id=conversation_id,
            message_id=message_id,
            content=content,
            thread_id=thread_id,
            parent_id=parent_message_id
        )
        
        try:
            # 执行Graph
            config = {"configurable": {"thread_id": thread_id}}
            
            logger.info(f"Executing graph for message {message_id[:8]}...")
            final_state = await self.graph.ainvoke(initial_state, config)
            
            # 保存线程状态（用于分支）
            self.thread_states[thread_id] = final_state
            
            # 获取响应
            response = final_state.get("graph_response", "")
            
            # 更新消息的响应
            self.conversation_manager.update_response(
                conversation_id, message_id, response
            )
            
            return {
                "success": True,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "thread_id": thread_id,
                "response": response,
                "session_id": session_id,
                "artifacts": {
                    "task_plan_id": final_state.get("task_plan_id"),
                    "result_ids": final_state.get("result_artifact_ids", [])
                }
            }
            
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            
            # 更新错误响应
            error_msg = f"Error: {str(e)}"
            self.conversation_manager.update_response(
                conversation_id, message_id, error_msg
            )
            
            return {
                "success": False,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "error": str(e)
            }
    
    async def handle_permission_confirmation(
        self,
        thread_id: str,
        approved: bool,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理工具权限确认
        
        Args:
            thread_id: 线程ID
            approved: 是否批准
            reason: 原因说明
            
        Returns:
            执行结果
        """
        try:
            # 获取当前状态
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await self.graph.aget_state(config)
            
            if not snapshot or not snapshot.values:
                raise ValueError(f"Thread {thread_id} not found or has no state")
            
            state = snapshot.values
            pending = state.get("pending_tool_confirmation")
            
            if not pending:
                raise ValueError("No pending tool confirmation")
            
            # 准备工具执行结果
            tool_name = pending["tool_name"]
            from_agent = pending["from_agent"]
            
            if approved:
                # 模拟执行工具（实际应该从registry获取toolkit）
                logger.info(f"Tool {tool_name} approved, executing...")
                
                # 这里简化处理，实际应该调用真实的工具
                # toolkit = self.get_agent_toolkit(from_agent)
                # result = await toolkit.execute_tool(tool_name, pending["params"])
                
                result = ToolResult(
                    success=True,
                    data={"message": f"Tool {tool_name} executed successfully (simulated)"}
                )
            else:
                # 创建拒绝结果
                result = ToolResult(
                    success=False,
                    error=f"Permission denied: {reason or 'User rejected'}"
                )
            
            # 更新状态
            update_values = {
                "pending_tool_confirmation": {
                    **pending,
                    "result": (tool_name, result)  # 添加结果
                },
                "next_agent": from_agent  # 返回原Agent继续执行
            }
            
            # 更新状态
            await self.graph.aupdate_state(config, update_values)
            
            # 继续执行
            logger.info(f"Resuming execution for thread {thread_id}")
            final_state = await self.graph.ainvoke(None, config)
            
            # 保存最终状态
            self.thread_states[thread_id] = final_state
            
            return {
                "success": True,
                "thread_id": thread_id,
                "response": final_state.get("graph_response", ""),
                "tool_executed": tool_name,
                "approved": approved
            }
            
        except Exception as e:
            logger.exception(f"Error handling permission: {e}")
            return {
                "success": False,
                "thread_id": thread_id,
                "error": str(e)
            }
    
    def get_conversation_history(
        self,
        conversation_id: str,
        branch_path: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        获取对话历史
        
        Args:
            conversation_id: 对话ID
            branch_path: 分支路径（消息ID列表）
            
        Returns:
            对话历史列表
        """
        if branch_path:
            # 指定路径
            messages = []
            for msg_id in branch_path:
                msg = self.conversation_manager.conversations.get(
                    conversation_id, {}
                ).get("messages", {}).get(msg_id)
                if msg:
                    messages.append({
                        "role": "user",
                        "content": msg["content"],
                        "message_id": msg["message_id"],
                        "timestamp": msg["timestamp"]
                    })
                    if msg["graph_response"]:
                        messages.append({
                            "role": "assistant",
                            "content": msg["graph_response"],
                            "timestamp": msg["timestamp"]
                        })
            return messages
        else:
            # 活跃分支
            path = self.conversation_manager.get_conversation_path(conversation_id)
            messages = []
            for msg in path:
                messages.append({
                    "role": "user",
                    "content": msg["content"],
                    "message_id": msg["message_id"],
                    "timestamp": msg["timestamp"]
                })
                if msg["graph_response"]:
                    messages.append({
                        "role": "assistant",
                        "content": msg["graph_response"],
                        "timestamp": msg["timestamp"]
                    })
            return messages
    
    def list_conversations(self) -> List[Dict[str, Any]]:
        """
        列出所有对话
        
        Returns:
            对话列表
        """
        conversations = []
        for conv_id, conv in self.conversation_manager.conversations.items():
            conversations.append({
                "conversation_id": conv_id,
                "created_at": conv["created_at"],
                "updated_at": conv["updated_at"],
                "message_count": len(conv["messages"]),
                "branch_count": len(conv["branches"])
            })
        return conversations
"""
Context管理器（重构版）
核心改进：
1. 添加对话历史格式化功能
2. 简化压缩逻辑
3. 增强routing context准备
"""

from typing import List, Dict, Optional, Any
from utils.logger import get_logger

logger = get_logger("Core")


class ContextManager:
    """
    上下文管理器
    负责：对话历史处理、消息压缩、路由上下文准备
    """
    
    # 压缩级别对应的最大字符数
    COMPRESSION_LEVELS = {
        'full': 100000,
        'normal': 40000,
        'compact': 20000,
        'minimal': 5000
    }
    
    @classmethod
    def format_conversation_history(
        cls,
        conversation_path: List[Dict],
        compression_level: str = "normal"
    ) -> str:
        """
        格式化对话历史为可读文本
        
        Args:
            conversation_path: 从根到当前的消息路径（UserMessage列表）
            compression_level: 压缩级别
            
        Returns:
            格式化的对话历史文本
        """
        if not conversation_path:
            return ""
        
        # 决定保留多少历史
        max_messages = {
            "full": 999,
            "normal": 10,
            "compact": 5,
            "minimal": 2
        }.get(compression_level, 10)
        
        # 如果超过限制，保留第一条（初始任务）+ 最近N-1条
        if len(conversation_path) > max_messages:
            selected = [conversation_path[0]] + conversation_path[-(max_messages-1):]
            truncated_count = len(conversation_path) - len(selected)
        else:
            selected = conversation_path
            truncated_count = 0
        
        # 格式化为Markdown
        lines = ["## Conversation History", ""]
        
        if truncated_count > 0:
            lines.append(f"_({truncated_count} earlier messages omitted)_")
            lines.append("")
        
        for i, msg in enumerate(selected, 1):
            lines.append(f"### Turn {i}")
            lines.append(f"**User**: {msg['content']}")
            
            if msg.get('graph_response'):
                # 限制响应长度避免过长
                response = msg['graph_response']
                if len(response) > 500:
                    response = response[:500] + "... _(truncated)_"
                lines.append(f"**Assistant**: {response}")
            
            lines.append("")  # 空行分隔
        
        return "\n".join(lines)
    
    @classmethod
    def compress_messages(
        cls,
        messages: List[Dict],
        level: str = "normal",
        preserve_recent: int = 5
    ) -> List[Dict]:
        """
        压缩工具交互历史（用于agent记忆）
        
        Args:
            messages: 消息列表
            level: 压缩级别
            preserve_recent: 保留最近N条完整消息
            
        Returns:
            压缩后的消息列表
        """
        if not messages or level == "full":
            return messages
        
        max_length = cls.COMPRESSION_LEVELS.get(level, 40000)
        
        # 计算总长度
        total_length = sum(len(msg.get("content", "")) for msg in messages)
        
        if total_length <= max_length:
            return messages
        
        logger.debug(f"Compressing messages: {total_length} chars -> max {max_length}")
        
        # 保留最近的N条消息
        if len(messages) <= preserve_recent:
            return messages
        
        recent_messages = messages[-preserve_recent:]
        older_messages = messages[:-preserve_recent]
        
        # 计算recent消息的长度
        recent_length = sum(len(msg.get("content", "")) for msg in recent_messages)
        remaining_length = max_length - recent_length
        
        if remaining_length <= 0:
            # recent消息已经超过限制，只保留recent
            return [{
                "role": "system",
                "content": f"[{len(older_messages)} earlier messages truncated]"
            }] + recent_messages
        
        # 从后往前保留older消息
        compressed = []
        current_length = 0
        
        for msg in reversed(older_messages):
            msg_length = len(msg.get("content", ""))
            if current_length + msg_length > remaining_length:
                if len(older_messages) > len(compressed):
                    compressed.insert(0, {
                        "role": "system",
                        "content": f"[{len(older_messages) - len(compressed)} earlier messages truncated]"
                    })
                break
            compressed.insert(0, msg)
            current_length += msg_length
        
        return compressed + recent_messages
    
    @classmethod
    def prepare_routing_context(
        cls,
        agent_name: str,
        state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        为Agent准备路由上下文
        
        Args:
            agent_name: Agent名称
            state: 当前Graph状态
            
        Returns:
            路由上下文字典（会传递给BaseAgent）
        """
        context = {
            "session_id": state.get("session_id"),
            "thread_id": state.get("thread_id"),
            "user_message_id": state.get("user_message_id"),
        }
        
        # 添加对话历史
        if state.get("conversation_history"):
            context["conversation_history"] = state["conversation_history"]
        
        # 添加subagent路由信息
        if state.get("subagent_route") and state.get("current_agent") != agent_name:
            context["routing_from"] = state.get("current_agent")
            context["routing_instruction"] = state["subagent_route"].get("instruction", "")
            logger.debug(f"{agent_name} received routing from {context['routing_from']}")
        
        return context
    
    @classmethod
    def should_compress(
        cls,
        messages: List[Dict],
        threshold: int = 15000
    ) -> bool:
        """判断是否需要压缩"""
        if not messages:
            return False
        
        total_length = sum(len(msg.get("content", "")) for msg in messages)
        return total_length > threshold
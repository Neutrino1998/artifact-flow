"""
Context管理器（重构版）
职责：通用的消息压缩和路由上下文准备
"""

from typing import List, Dict, Optional, Any
from utils.logger import get_logger

logger = get_logger("Core")


class ContextManager:
    """
    上下文管理器
    职责：
    1. 通用的消息压缩（可用于任何消息列表）
    2. 路由上下文准备
    """
    
    # 压缩级别对应的最大字符数
    COMPRESSION_LEVELS = {
        'full': 100000,
        'normal': 40000,
        'compact': 20000,
        'minimal': 5000
    }
    
    @classmethod
    def compress_messages(
        cls,
        messages: List[Dict],
        level: str = "normal",
        preserve_recent: int = 5
    ) -> List[Dict]:
        """
        压缩消息历史（通用方法）
        
        可用于：
        - 对话历史压缩
        - Agent工具交互历史压缩
        
        Args:
            messages: 消息列表（需要有"content"字段）
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
        
        logger.debug(f"Compressing {len(messages)} messages: {total_length} chars -> max {max_length}")
        
        # 保留最近的N条消息
        if len(messages) <= preserve_recent:
            return messages
        
        recent_messages = messages[-preserve_recent:]
        older_messages = messages[:-preserve_recent]
        
        # 计算recent消息的长度
        recent_length = sum(len(msg.get("content", "")) for msg in recent_messages)
        remaining_length = max_length - recent_length
        
        if remaining_length <= 0:
            # recent消息已经超过限制，只保留recent + 截断提示
            return [{
                "role": "system",
                "content": f"[{len(older_messages)} earlier messages truncated due to length limit]"
            }] + recent_messages
        
        # 从后往前保留older消息，直到达到限制
        compressed = []
        current_length = 0
        
        for msg in reversed(older_messages):
            msg_length = len(msg.get("content", ""))
            if current_length + msg_length > remaining_length:
                # 达到限制，添加截断提示
                if len(older_messages) > len(compressed):
                    compressed.insert(0, {
                        "role": "system",
                        "content": f"[{len(older_messages) - len(compressed)} earlier messages truncated]"
                    })
                break
            compressed.insert(0, msg)
            current_length += msg_length
        
        result = compressed + recent_messages
        logger.debug(f"Compressed to {len(result)} messages")
        return result
    
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
            路由上下文字典
        """
        context = {
            "session_id": state.get("session_id"),
            "thread_id": state.get("thread_id"),
            "user_message_id": state.get("user_message_id"),
        }
        
        # 添加对话历史（已经格式化好的文本）
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
        level: str = "normal"
    ) -> bool:
        """判断是否需要压缩"""
        if not messages:
            return False
        
        max_length = cls.COMPRESSION_LEVELS.get(level, 40000)
        total_length = sum(len(msg.get("content", "")) for msg in messages)
        return total_length > max_length
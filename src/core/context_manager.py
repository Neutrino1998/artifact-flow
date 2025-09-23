"""
Context管理器
负责消息压缩和上下文准备
"""

from typing import List, Dict, Optional, Any
from utils.logger import get_logger

logger = get_logger("Core")


class ContextManager:
    """
    上下文压缩管理器
    Phase 1: 字符长度截断
    Phase 2: 智能摘要（TODO）
    """
    
    # 压缩级别对应的最大字符数
    COMPRESSION_LEVELS = {
        'full': 100000,      # 完整上下文
        'normal': 40000,    # 标准压缩
        'compact': 20000,   # 紧凑模式
        'minimal': 5000     # 最小化
    }
    
    @classmethod
    def compress_messages(
        cls,
        messages: List[Dict],
        level: str = "normal",
        preserve_recent: int = 5
    ) -> List[Dict]:
        """
        压缩消息历史（只作用于工具交互历史）
        
        Args:
            messages: 消息列表
            level: 压缩级别
            preserve_recent: 保留最近N条完整消息
            
        Returns:
            压缩后的消息列表
        """
        if not messages:
            return messages
        
        # 完整模式，不压缩
        if level == "full":
            return messages
        
        max_length = cls.COMPRESSION_LEVELS.get(level, 20000)
        
        # 计算总长度
        total_length = sum(len(msg.get("content", "")) for msg in messages)
        
        # 如果未超过限制，直接返回
        if total_length <= max_length:
            return messages
        
        logger.debug(f"Compressing messages: {total_length} chars -> max {max_length}")
        
        # 保留最近的N条消息
        if len(messages) <= preserve_recent:
            return messages
        
        recent_messages = messages[-preserve_recent:] if preserve_recent > 0 else []
        older_messages = messages[:-preserve_recent] if preserve_recent > 0 else messages
        
        # 计算recent消息的长度
        recent_length = sum(len(msg.get("content", "")) for msg in recent_messages)
        remaining_length = max_length - recent_length
        
        if remaining_length <= 0:
            # 如果recent消息已经超过限制，只保留recent
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
                # 添加截断提示
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
    def prepare_context_for_agent(
        cls,
        agent_name: str,
        state: Dict[str, Any],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        为Agent准备上下文
        
        Args:
            agent_name: Agent名称
            state: 当前Graph状态
            additional_context: 额外的上下文信息
            
        Returns:
            准备好的上下文字典
        """
        context = additional_context or {}
        
        # 1. 添加task_plan（如果存在）
        try:
            from tools.implementations.artifact_ops import _artifact_store
            
            # 所有Agent都能看到task_plan
            task_plan = _artifact_store.get("task_plan")
            if task_plan:
                context["task_plan_content"] = task_plan.content
                context["task_plan_version"] = task_plan.current_version
                context["task_plan_updated"] = task_plan.updated_at.isoformat()
                logger.debug(f"{agent_name} loaded task_plan (v{task_plan.current_version})")
            
            # Lead Agent额外获取artifacts列表
            if agent_name == "lead_agent":
                artifacts_list = _artifact_store.list_artifacts()
                if artifacts_list:
                    context["artifacts_inventory"] = artifacts_list
                    context["artifacts_count"] = len(artifacts_list)
                    logger.debug(f"Lead Agent loaded {len(artifacts_list)} artifacts inventory")
        except Exception as e:
            logger.debug(f"Context preparation partial failure: {e}")
        
        # 2. 添加路由信息（如果是被路由到的Agent）
        if state.get("routing_info") and state.get("last_agent") != agent_name:
            context["routing_from"] = state.get("last_agent")
            context["routing_instruction"] = state["routing_info"].get("instruction", "")
        
        # 3. 添加压缩级别
        context["compression_level"] = state.get("compression_level", "normal")
        
        # 4. 添加会话信息
        context["session_id"] = state.get("session_id")
        context["thread_id"] = state.get("thread_id")
        
        return context
    
    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """
        估算token数（简单实现）
        
        Args:
            text: 文本内容
            
        Returns:
            估算的token数
        """
        # 粗略估算：
        # 英文：平均每4个字符一个token
        # 中文：平均每2个字符一个token
        # 这里用3作为平均值
        return len(text) // 3
    
    @classmethod
    def should_compress(
        cls,
        messages: List[Dict],
        threshold: int = 15000
    ) -> bool:
        """
        判断是否需要压缩
        
        Args:
            messages: 消息列表
            threshold: 字符数阈值
            
        Returns:
            是否需要压缩
        """
        if not messages:
            return False
        
        total_length = sum(len(msg.get("content", "")) for msg in messages)
        return total_length > threshold
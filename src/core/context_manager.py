"""
Context管理器
职责：通用的消息压缩和路由上下文准备
"""

from typing import List, Dict, Optional, Any, Tuple
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ContextManager:
    """
    上下文管理器
    职责：
    1. 通用的消息压缩（可用于任何消息列表）
    2. 路由上下文准备
    """
    
    # 压缩级别对应的最大字符数
    COMPRESSION_LEVELS = {
        'full': 160000,
        'normal': 80000,
        'compact': 40000,
        'minimal': 20000
    }
    
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
    def prepare_agent_context(
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
        
        # 注入task_plan和artifacts
        try:
            from tools.implementations.artifact_ops import _artifact_store
            
            if context.get("session_id"):
                _artifact_store.set_session(context["session_id"])
            
            task_plan = _artifact_store.get("task_plan")
            if task_plan:
                context["task_plan_content"] = task_plan.content
                context["task_plan_version"] = task_plan.current_version
                context["task_plan_updated"] = task_plan.updated_at.isoformat()
            
            if agent_name == "lead_agent":
                artifacts_list = _artifact_store.list_artifacts()
                if artifacts_list:
                    context["artifacts_inventory"] = artifacts_list
                    context["artifacts_count"] = len(artifacts_list)
        except Exception as e:
            logger.debug(f"Context preparation partial failure: {e}")
        
        # 添加subagent路由信息
        if pending := state.get("subagent_pending"):
            if pending.get("target") == agent_name:
                context["routing_from"] = "lead_agent"
                context["routing_instruction"] = pending.get("instruction", "")
        
        return context
    
    @classmethod
    def build_agent_messages(
        cls,
        agent: Any,  # BaseAgent实例
        state: Dict[str, Any],
        instruction: str,
        tool_interactions: Optional[List[Dict]] = None,
        pending_tool_result: Optional[Tuple[str, Any]] = None,
    ) -> List[Dict]:
        """
        统一构建Agent messages
        
        拼接顺序：
        system → conversation_history → instruction → tool_interactions → tool_result
        """
        messages = []
        compression_level = state.get("compression_level", "normal")
        
        # Part 1: System prompt
        context = cls.prepare_agent_context(agent.config.name, state)
        system_prompt = agent.build_complete_system_prompt(context)
        messages.append({"role": "system", "content": system_prompt})
        
        # Part 2: Conversation history
        # 重要：保留偶数条消息以确保完整的 [user, assistant] 对话对
        # 例如: preserve_recent=4 保留最近2轮完整对话
        if agent.config.name == "lead_agent":   # 仅Lead Agent需要
            if history := state.get("conversation_history"):
                compressed = cls.compress_messages(
                    history,
                    level=compression_level,
                    preserve_recent=4   # 偶数：[user, asst, user, asst]
                )
                if len(compressed) < len(history):
                    compressed = cls._merge_truncation_marker(
                        compressed,
                        "_[Earlier conversation truncated]_"
                    )
                messages.extend(compressed)
        
        # Part 3: Current instruction
        messages.append({"role": "user", "content": instruction})
        
        # Part 4: Tool interactions
        # 重要：保留奇数条消息以确保开头和结尾都是assistant
        # 因为前后的user消息会单独组装 (instruction 和 tool_result)
        if tool_interactions:
            compressed = cls.compress_messages(
                tool_interactions,
                level=compression_level,
                preserve_recent=5   # 奇数：[asst, user, asst, user, asst]
            )
            if len(compressed) < len(tool_interactions):
                compressed = cls._merge_truncation_marker(
                    compressed,
                    "_[Earlier tool calls truncated]_"
                )
            messages.extend(compressed)
        
        # Part 5: Pending tool result
        if pending_tool_result:
            tool_name, result = pending_tool_result
            from tools.prompt_generator import format_result
            tool_result_text = format_result(tool_name, result.to_dict())
            messages.append({"role": "user", "content": tool_result_text})
        
        return messages

    @classmethod
    def _merge_truncation_marker(cls, messages: List[Dict], marker: str) -> List[Dict]:
        """将截断标记合并到第一条消息，避免破坏角色交替"""
        if not messages:
            return messages
        
        first = messages[0].copy()
        first["content"] = f"{marker}\n\n{first['content']}"
        return [first] + messages[1:]
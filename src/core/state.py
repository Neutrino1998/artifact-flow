"""
执行状态 — 简化版

设计文档 §替代 LangGraph：
- 5 值枚举简化为一个布尔值 `completed`
- agent 切换靠 `current_agent`
- message_id 同时作为执行标识（1:1 关系）
"""

from typing import Optional, Dict, Any, List
from core.events import create_initial_metrics


def create_initial_state(
    task: str,
    session_id: str,
    message_id: str,
    conversation_history: List[Dict[str, str]],
    always_allowed_tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    创建初始执行状态

    Args:
        task: 用户输入内容
        session_id: artifact session ID（与 conversation_id 相同）
        message_id: 消息 ID，同时作为执行标识
        conversation_history: 格式化后的对话历史 [{"role": ..., "content": ...}]
        always_allowed_tools: 从上一条消息 metadata 中恢复的 always allow 工具列表

    Returns:
        执行状态字典
    """
    return {
        "current_task": task,
        "session_id": session_id,
        "message_id": message_id,
        "conversation_history": conversation_history,
        "completed": False,
        "error": False,            # 执行是否以错误终止
        "current_agent": "lead_agent",
        "always_allowed_tools": list(always_allowed_tools) if always_allowed_tools else [],
        "events": [],             # 内存事件列表，执行完 batch write
        "queued_messages": [],     # 执行中注入的消息
        "execution_metrics": create_initial_metrics(),
        "response": "",            # 最终响应
    }

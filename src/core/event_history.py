"""
EventHistory — 从 ExecutionEvent 列表构建 LLM messages

核心语义：
- 按 agent_name 过滤事件（lead / sub 各看各的）
- 从右往左扫描，在最近一次"边界"处停：
  * COMPACTION_SUMMARY：所有 agent 都视为硬边界（此前事件全部由 summary 覆盖）
  * SUBAGENT_INSTRUCTION with fresh_start=True：仅对 subagent 生效（之前 session 隔离）
- 从边界（含）到末尾的事件转成 messages
"""

from typing import List, Dict, Any

from core.events import StreamEventType, ExecutionEvent


LEAD_AGENT = "lead_agent"


def build_event_history(
    events: List[ExecutionEvent],
    agent_name: str,
) -> List[Dict[str, Any]]:
    """
    从事件列表构建指定 agent 的 LLM messages。

    Args:
        events: 完整事件流（历史 + 当前轮，含 is_historical 混合）
        agent_name: 目标 agent 名（过滤非本 agent 的事件）

    Returns:
        LLM 消息列表 [{"role": "user"/"assistant", "content": ..., "_meta"?: {...}}]
    """
    filtered = [e for e in events if e.agent_name == agent_name]
    if not filtered:
        return []

    boundary_idx = _find_boundary(filtered, is_subagent=agent_name != LEAD_AGENT)
    return _events_to_messages(filtered[boundary_idx:])


def _find_boundary(events: List[ExecutionEvent], is_subagent: bool) -> int:
    """
    从右往左扫描，返回第一个边界事件的索引（含）。
    没有边界返回 0（历史从头取）。
    """
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        if ev.event_type == StreamEventType.COMPACTION_SUMMARY.value:
            return i
        if is_subagent and ev.event_type == StreamEventType.SUBAGENT_INSTRUCTION.value:
            data = ev.data or {}
            if data.get("fresh_start", False):
                return i
    return 0


def _events_to_messages(events: List[ExecutionEvent]) -> List[Dict[str, Any]]:
    """将事件列表转成 LLM 消息。"""
    from tools.xml_formatter import format_result

    messages: List[Dict[str, Any]] = []
    for ev in events:
        data = ev.data or {}
        et = ev.event_type

        if et == StreamEventType.COMPACTION_SUMMARY.value:
            content = data.get("content", "")
            if content:
                messages.append({"role": "user", "content": content})

        elif et == StreamEventType.USER_INPUT.value:
            content = data.get("content", "")
            if content:
                messages.append({"role": "user", "content": content})

        elif et == StreamEventType.SUBAGENT_INSTRUCTION.value:
            instruction = data.get("instruction", "")
            if instruction:
                messages.append({"role": "user", "content": instruction})

        elif et == StreamEventType.QUEUED_MESSAGE.value:
            content = data.get("content", "")
            if content:
                messages.append({"role": "user", "content": content})

        elif et == StreamEventType.LLM_COMPLETE.value:
            content = data.get("content", "")
            if content:
                msg: Dict[str, Any] = {"role": "assistant", "content": content}
                token_usage = data.get("token_usage")
                if token_usage:
                    msg["_meta"] = {
                        "input_tokens": token_usage.get("input_tokens", 0),
                        "output_tokens": token_usage.get("output_tokens", 0),
                    }
                messages.append(msg)

        elif et == StreamEventType.TOOL_COMPLETE.value:
            tool_name = data.get("tool", "unknown")
            result_data = {
                "success": data.get("success", False),
                "data": data.get("result_data"),
                "error": data.get("error"),
            }
            result_text = format_result(tool_name, result_data)
            messages.append({"role": "user", "content": result_text})

    return messages

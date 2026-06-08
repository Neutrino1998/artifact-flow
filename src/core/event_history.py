"""
EventHistory — 从 ExecutionEvent 列表构建 LLM messages

核心语义：
- 按 agent_name 过滤事件（lead / sub 各看各的）
- 从右往左扫描，在最近一次"边界"处停：
  * COMPACTION_SUMMARY with success=True：所有 agent 都视为硬边界
    （success=False 是 compaction LLM 失败的占位事件，仅用于 start/summary
     配对 + UI 展示，对历史完全透明 —— 既不作 boundary，也不入 messages）
  * SUBAGENT_INSTRUCTION with fresh_start=True：仅对 subagent 生效（之前 session 隔离）
- 从边界（含）到末尾的事件转成 messages
"""

from typing import List, Dict, Any

from core.events import StreamEventType, ExecutionEvent


LEAD_AGENT = "lead_agent"


def build_event_history(
    events: List[ExecutionEvent],
    agent_name: str,
    vision_blocks: Dict[Any, str] | None = None,
    vision_capable: bool = True,
) -> List[Dict[str, Any]]:
    """
    从事件列表构建指定 agent 的 LLM messages。

    Args:
        events: 完整事件流（历史 + 当前轮，含 is_historical 混合）
        agent_name: 目标 agent 名（过滤非本 agent 的事件）
        vision_blocks: 本 turn 的图块缓存 ``{(artifact_id, version): data_uri}``（由引擎
            在 read_artifact 读图后填进 state，纯内存、不持久化）。识图事件只存**引用**,
            在此对照缓存还原:命中(本轮读过)→ content 扩成图块列表;未命中(跨轮、
            state 已空)→ 文本占位。**纯内存查表,无 DB IO**——保持本函数纯净。
        vision_capable: 目标 agent 的模型是否支持识图(models.yaml `vision: true`)。
            False 时即便命中缓存也**不**注入图块,降级为占位文本——文本模型收到
            image_url 块会被 provider 端拒。默认 True 便于直接调用/测试(生产由
            context_manager 据 agent 模型显式计算后传入)。

    Returns:
        LLM 消息列表 [{"role": "user"/"assistant", "content": ..., "_meta"?: {...}}]
        识图命中时 content 是块列表 [{type:text}, {type:image_url}],否则为 str。
    """
    filtered = [e for e in events if e.agent_name == agent_name]
    if not filtered:
        return []

    boundary_idx = _find_boundary(filtered, is_subagent=agent_name != LEAD_AGENT)
    return _events_to_messages(
        filtered[boundary_idx:], vision_blocks or {}, vision_capable
    )


def last_llm_usage(events: List[ExecutionEvent], agent_name: str) -> int | None:
    """最近一次 llm_complete 的 input+output（compaction 触发口径），无则 None。

    与 build_event_history 同样按 agent 过滤、并只看最近 compaction 边界之后的事件
    （刚压缩完、边界后还没新 call → None）。但**直接读原始 ExecutionEvent 的
    token_usage**，不经 _events_to_messages —— 后者仅在 content 非空时才保留 _meta，
    会让「高 input + 空 content（如仅 reasoning 的回复）」漏报。token 记账与 response
    文本是否为空无关，故在此解耦。供 context_manager 的 <context_usage> 水位预警取数。
    """
    filtered = [e for e in events if e.agent_name == agent_name]
    if not filtered:
        return None

    boundary_idx = _find_boundary(filtered, is_subagent=agent_name != LEAD_AGENT)
    for ev in reversed(filtered[boundary_idx:]):
        if ev.event_type == StreamEventType.LLM_COMPLETE.value:
            token_usage = (ev.data or {}).get("token_usage")
            if token_usage:
                return token_usage.get("input_tokens", 0) + token_usage.get("output_tokens", 0)
    return None


def _find_boundary(events: List[ExecutionEvent], is_subagent: bool) -> int:
    """
    从右往左扫描，返回第一个边界事件的索引（含）。
    没有边界返回 0（历史从头取）。
    """
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        data = ev.data or {}
        if ev.event_type == StreamEventType.COMPACTION_SUMMARY.value:
            if data.get("success", True):
                return i
            continue
        if is_subagent and ev.event_type == StreamEventType.SUBAGENT_INSTRUCTION.value:
            if data.get("fresh_start", False):
                return i
    return 0


def _events_to_messages(
    events: List[ExecutionEvent],
    vision_blocks: Dict[Any, str],
    vision_capable: bool = True,
) -> List[Dict[str, Any]]:
    """将事件列表转成 LLM 消息。"""
    from tools.xml_formatter import format_result

    messages: List[Dict[str, Any]] = []
    for ev in events:
        data = ev.data or {}
        et = ev.event_type

        if et == StreamEventType.COMPACTION_SUMMARY.value:
            if not data.get("success", True):
                continue  # failure marker — paired with compaction_start, ignored by history
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
                "parser_warnings": data.get("parser_warnings"),
            }
            result_text = format_result(tool_name, result_data)

            # 识图:tool_complete 携图片引用(metadata.image,仅 id/version/content_type)。
            # 对照本 turn 的 vision_blocks 缓存还原——命中(本轮读过)→ content 扩成
            # [文本块, 图块];未命中(跨轮、state 已空)→ 文本附「再 read 即可重看」占位。
            img = (data.get("metadata") or {}).get("image")
            if isinstance(img, dict) and img.get("content_type"):
                key = (img.get("artifact_id"), img.get("version"))
                data_uri = vision_blocks.get(key)
                # vision_capable 门控:仅当 (a) 本轮缓存命中 且 (b) 目标模型支持识图,
                # 才扩成图块列表;否则一律降级占位文本——文本模型收 image_url 块会被
                # provider 端拒(见 model_supports_vision)。
                if data_uri and vision_capable:
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": result_text},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ]})
                    continue
                # 占位文案分两种,语义不同,绝不可混用:
                #   - 模型识图但缓存未命中(跨轮、state 已空):重读即可重新看到 →「需要再看就重读」。
                #   - 模型不识图(文本模型):重读也永远看不到,别诱导无效重读;主体是模型自身(you)。
                if vision_capable:
                    note = (
                        f"(image not shown — re-read artifact "
                        f"'{img.get('artifact_id')}' if you need to view it)"
                    )
                else:
                    note = (
                        "(image not shown — you can't view images because this "
                        "model is not multimodal)"
                    )
                result_text = f"{result_text}\n{note}"

            messages.append({"role": "user", "content": result_text})

    return messages

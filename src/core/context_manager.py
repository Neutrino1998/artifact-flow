"""
ContextManager — 唯一的抽象

设计文档 §唯一的抽象：ContextManager.build()

职责：
1. 拼接 system prompt（role_prompt + system_info + task_plan + artifacts + agents + tools）
2. 构建对话历史（含跨轮 compaction）
3. 构建当前输入（含 queued messages 合并、tool 交互）
4. 按 agent_name 过滤事件构建 context
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@dataclass
class Context:
    """构建好的 context"""
    messages: List[Dict[str, str]]


class ContextManager:
    """
    上下文管理器

    职责：
    1. 为每次 LLM 调用构建完整的 messages
    2. 通用的消息压缩
    """

    # 压缩级别对应的最大字符数
    COMPRESSION_LEVELS = {
        'full': 160000,
        'normal': 80000,
        'compact': 40000,
        'minimal': 20000
    }

    @classmethod
    def build(
        cls,
        state: Dict[str, Any],
        agent_config: Any,  # AgentConfig
        agents: Dict[str, Any],  # {name: AgentConfig} for building available agents section
        tools: Dict[str, Any],   # {name: BaseTool}
        artifact_manager: Optional[Any] = None,
        artifacts_inventory: Optional[List[Dict]] = None,
        context_max_chars: int = 80000,
        compaction_preserve_pairs: int = 2,
        tool_interaction_preserve: int = 6,
    ) -> Context:
        """
        构建 LLM 调用所需的完整 messages

        设计文档 §唯一的抽象：ContextManager.build()

        Args:
            state: 执行状态
            agent_config: 当前 agent 的 AgentConfig
            agents: 所有 agent 配置 {name: AgentConfig}
            tools: 所有可用工具 {name: BaseTool}
            artifact_manager: ArtifactManager（用于 artifacts 清单）
            artifacts_inventory: 预加载的 artifacts 清单

        Returns:
            Context（含 messages 列表）
        """
        from tools.xml_formatter import generate_tool_instruction

        # ========== System Prompt ==========
        system_parts = []

        # 1. 角色提示词（MD body）
        if agent_config.role_prompt:
            system_parts.append(agent_config.role_prompt)

        # 2. 系统时间
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")
        system_parts.append(f'<system_time>Current time: {current_time}</system_time>')

        # 3. 任务计划（从 artifacts 注入）
        task_plan_content = cls._extract_task_plan(artifacts_inventory)
        if task_plan_content:
            system_parts.append(f'<team_task_plan>\n{task_plan_content}\n</team_task_plan>')

        # 4. Artifact 清单（条件注入：仅有 artifact 工具的 agent）
        has_artifact_tools = any(t in agent_config.tools for t in [
            "create_artifact", "update_artifact", "rewrite_artifact", "read_artifact"
        ])
        if has_artifact_tools and artifacts_inventory:
            system_parts.append(cls._build_artifacts_inventory(artifacts_inventory))

        # 5. 可用 Agent 列表（条件注入：仅有 call_subagent 工具的 agent）
        if "call_subagent" in agent_config.tools:
            system_parts.append(cls._build_available_agents(agents, agent_config.name))

        # 6. 工具说明
        tool_names = list(agent_config.tools.keys())
        agent_tools = [tools[name] for name in tool_names if name in tools]
        if agent_tools:
            system_parts.append(generate_tool_instruction(agent_tools))

        system_prompt = "\n\n".join(s for s in system_parts if s)

        # ========== Messages ==========
        messages = [{"role": "system", "content": system_prompt}]

        # 两个 agent 类型共用的输入构建
        current_input_messages, anchor_idx = cls._build_current_input(state, agent_config)

        # Lead 独有：conversation history（DB Message 层，含 compaction summaries）
        if agent_config.name == "lead_agent":
            history = state.get("conversation_history", [])
            if history:
                tool_chars = sum(len(m.get("content", "")) for m in current_input_messages)
                history_budget = max(context_max_chars - tool_chars, 20000)
                preserve_recent = compaction_preserve_pairs * 2
                compressed = cls.compress_messages_with_budget(
                    history, history_budget, preserve_recent=preserve_recent
                )
                if len(compressed) < len(history):
                    compressed = cls._merge_truncation_marker(compressed, "...")
                messages.extend(compressed)

        # 共用：预算感知的 tool interaction 追加
        #
        # 不可丢弃的"锚点消息"：
        # - lead: current_input_messages[0] (user_input，只有一条)
        # - sub:  最后一个 subagent_instruction（当前正在执行的任务）
        #         早期的 instruction 及其交互已被后续 llm_complete 消化，可丢弃
        if current_input_messages:
            anchor_msg = current_input_messages[anchor_idx]

            remaining = context_max_chars - sum(len(m.get("content", "")) for m in messages)
            tool_total = sum(len(m.get("content", "")) for m in current_input_messages)
            if tool_total <= remaining:
                messages.extend(current_input_messages)
            elif remaining > 0:
                anchor_len = len(anchor_msg.get("content", ""))
                tool_budget = max(remaining - anchor_len, 0)

                # anchor 之前的消息（早期轮次）和 anchor 之后的消息（当前轮工具交互）
                before_anchor = current_input_messages[:anchor_idx]
                after_anchor = current_input_messages[anchor_idx + 1:]
                compressible = before_anchor + after_anchor

                if tool_budget > 0 and compressible:
                    # after_anchor 是当前轮交互，优先级更高，先分配预算
                    preserve = max(tool_interaction_preserve, 2)
                    if after_anchor:
                        compressed_after = cls.compress_messages_with_budget(
                            after_anchor, tool_budget, preserve_recent=preserve
                        )
                        if len(compressed_after) < len(after_anchor):
                            compressed_after = cls._merge_truncation_marker(compressed_after, "...")
                        after_used = sum(len(m.get("content", "")) for m in compressed_after)
                    else:
                        compressed_after = []
                        after_used = 0

                    before_budget = tool_budget - after_used
                    if before_anchor and before_budget > 0:
                        compressed_before = cls.compress_messages_with_budget(
                            before_anchor, before_budget, preserve_recent=preserve
                        )
                        if len(compressed_before) < len(before_anchor):
                            compressed_before = cls._merge_truncation_marker(compressed_before, "...")
                    else:
                        compressed_before = []

                    messages.extend(compressed_before)
                    messages.append(anchor_msg)
                    messages.extend(compressed_after)
                else:
                    if anchor_len > remaining:
                        anchor_msg = {**anchor_msg, "content": anchor_msg["content"][:remaining]}
                    messages.append(anchor_msg)
            else:
                # remaining <= 0: 预算已耗尽，只保留锚点消息
                messages.append(anchor_msg)

        return Context(messages=messages)

    @classmethod
    def _extract_task_plan(cls, artifacts_inventory: Optional[List[Dict]]) -> Optional[str]:
        """从 artifacts 清单中提取 task_plan 内容"""
        if not artifacts_inventory:
            return None

        for artifact in artifacts_inventory:
            if artifact.get("id") == "task_plan" and artifact.get("content"):
                return artifact["content"]
        return None

    @classmethod
    def _build_artifacts_inventory(cls, artifacts_inventory: List[Dict]) -> str:
        """构建 artifacts 清单部分"""
        count = len(artifacts_inventory)
        lines = [f'{count} artifact(s) in this session.']
        lines.append('<artifacts_inventory>')
        for artifact in artifacts_inventory:
            source = artifact.get("source", "agent")
            lines.append(
                f'<artifact version="{artifact["version"]}" type="{artifact["content_type"]}" '
                f'source="{source}" updated="{artifact["updated_at"]}">'
            )
            lines.append(f'<id>{artifact["id"]}</id>')
            lines.append(f'<title>{artifact["title"]}</title>')
            lines.append(artifact.get("content", ""))
            lines.append('</artifact>')
        lines.append(
            '\nArtifacts with source: user_upload are documents uploaded by the user '
            '— use `read_artifact` for full content if relevant.'
        )
        lines.append('</artifacts_inventory>')
        return '\n'.join(lines)

    @classmethod
    def _build_available_agents(cls, agents: Dict[str, Any], current_agent: str) -> str:
        """构建可用 agent 列表"""
        sub_agents = {n: c for n, c in agents.items() if n != current_agent and not c.internal}
        if not sub_agents:
            return "<note>No sub-agents are currently registered. Work independently.</note>"

        lines = ["<available_subagents>"]
        lines.append("Use the `call_subagent` tool to delegate tasks. Provide clear, specific instructions.\n")

        for name, config in sub_agents.items():
            lines.append(f'<agent name="{name}">')
            lines.append(config.description.rstrip())
            lines.append("</agent>")

        lines.append("</available_subagents>")
        return "\n".join(lines)

    @classmethod
    def _build_current_input(cls, state: Dict[str, Any], agent_config: Any) -> tuple:
        """
        Build raw messages from events (no compression). Both lead and sub.

        Returns:
            (messages, anchor_index) — anchor_index 指向不可丢弃的锚点消息：
            - lead: user_input（只有一条，index 0）
            - sub:  最后一个 subagent_instruction（当前正在执行的任务）
        """
        from core.events import StreamEventType

        events = state.get("events", [])
        messages = cls._build_tool_interactions(events, agent_config.name)

        # 从事件流直接定位最后一个 anchor 事件的 index
        anchor_types = {StreamEventType.USER_INPUT.value, StreamEventType.SUBAGENT_INSTRUCTION.value}
        agent_events = [e for e in events if e.agent_name == agent_config.name]

        # 映射：哪些事件产出了 messages（跳过空 content 的事件）
        anchor_idx = 0
        msg_idx = 0
        for event in agent_events:
            produced = cls._event_produces_message(event)
            if produced:
                if event.event_type in anchor_types:
                    anchor_idx = msg_idx
                msg_idx += 1

        return messages, anchor_idx

    @staticmethod
    def _event_produces_message(event) -> bool:
        """判断事件是否会在 _build_tool_interactions 中产出一条消息"""
        from core.events import StreamEventType

        et = event.event_type
        if et == StreamEventType.USER_INPUT.value:
            return bool(event.data and event.data.get("content"))
        if et == StreamEventType.SUBAGENT_INSTRUCTION.value:
            return bool(event.data and event.data.get("instruction"))
        if et == StreamEventType.LLM_COMPLETE.value:
            return bool(event.data and event.data.get("content"))
        if et == StreamEventType.QUEUED_MESSAGE.value:
            return bool(event.data and event.data.get("content"))
        if et == StreamEventType.TOOL_COMPLETE.value:
            return bool(event.data)
        return False

    @classmethod
    def _build_tool_interactions(cls, events: List, agent_name: str) -> List[Dict[str, str]]:
        """
        从事件流中构建 tool 交互历史

        按 agent_name 过滤事件，处理事件类型：
        - user_input → user 消息（用户原始输入，lead only）
        - subagent_instruction → user 消息（subagent 的 instruction）
        - queued_message → user 消息（执行中注入）
        - llm_complete → assistant 消息
        - tool_complete → user 消息
        """
        from tools.xml_formatter import format_result
        from core.events import StreamEventType

        interactions = []
        agent_events = [e for e in events if e.agent_name == agent_name]

        for event in agent_events:
            if event.event_type == StreamEventType.USER_INPUT.value:
                # 用户原始输入 → user 消息
                content = event.data.get("content", "") if event.data else ""
                if content:
                    interactions.append({"role": "user", "content": content})

            elif event.event_type == StreamEventType.SUBAGENT_INSTRUCTION.value:
                # Subagent instruction → user 消息
                instruction = event.data.get("instruction", "") if event.data else ""
                if instruction:
                    interactions.append({"role": "user", "content": instruction})

            elif event.event_type == StreamEventType.LLM_COMPLETE.value:
                # LLM 响应 → assistant 消息
                content = event.data.get("content", "") if event.data else ""
                if content:
                    interactions.append({"role": "assistant", "content": content})

            elif event.event_type == StreamEventType.QUEUED_MESSAGE.value:
                # 执行中注入的用户消息 → user 消息
                content = event.data.get("content", "") if event.data else ""
                if content:
                    interactions.append({"role": "user", "content": content})

            elif event.event_type == StreamEventType.TOOL_COMPLETE.value:
                # 工具结果 → user 消息
                if event.data:
                    tool_name = event.data.get("tool", "unknown")
                    result_data = {
                        "success": event.data.get("success", False),
                        "data": event.data.get("result_data"),
                        "error": event.data.get("error"),
                    }
                    result_text = format_result(tool_name, result_data)
                    interactions.append({"role": "user", "content": result_text})

        return interactions

    # ============================================================
    # 消息压缩（复用旧 ContextManager 的逻辑）
    # ============================================================

    @classmethod
    def compress_messages_with_budget(
        cls,
        messages: List[Dict],
        max_chars: int,
        preserve_recent: int = 4
    ) -> List[Dict]:
        """
        按绝对字符预算压缩消息历史

        Args:
            messages: 消息列表
            max_chars: 最大字符数预算
            preserve_recent: 保留最近 N 条完整消息

        Returns:
            压缩后的消息列表
        """
        if not messages:
            return messages

        total_length = sum(len(msg.get("content", "")) for msg in messages)
        if total_length <= max_chars:
            return messages

        logger.debug(f"Compressing {len(messages)} messages: {total_length} chars -> budget {max_chars}")

        if len(messages) <= preserve_recent:
            # Still over budget — hard-truncate older messages from the front
            return cls._hard_truncate_to_budget(messages, max_chars)

        recent_messages = messages[-preserve_recent:]
        older_messages = messages[:-preserve_recent]

        recent_length = sum(len(msg.get("content", "")) for msg in recent_messages)
        remaining_length = max_chars - recent_length

        if remaining_length <= 0:
            return [{
                "role": "system",
                "content": f"[{len(older_messages)} earlier messages truncated due to length limit]"
            }] + recent_messages

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
    def compress_messages(
        cls,
        messages: List[Dict],
        level: str = "normal",
        preserve_recent: int = 5
    ) -> List[Dict]:
        """
        压缩消息历史

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
        total_length = sum(len(msg.get("content", "")) for msg in messages)

        if total_length <= max_length:
            return messages

        logger.debug(f"Compressing {len(messages)} messages: {total_length} chars -> max {max_length}")

        if len(messages) <= preserve_recent:
            return messages

        recent_messages = messages[-preserve_recent:]
        older_messages = messages[:-preserve_recent]

        recent_length = sum(len(msg.get("content", "")) for msg in recent_messages)
        remaining_length = max_length - recent_length

        if remaining_length <= 0:
            return [{
                "role": "system",
                "content": f"[{len(older_messages)} earlier messages truncated due to length limit]"
            }] + recent_messages

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

        result = compressed + recent_messages
        logger.debug(f"Compressed to {len(result)} messages")
        return result

    @classmethod
    def _hard_truncate_to_budget(cls, messages: List[Dict], max_chars: int) -> List[Dict]:
        """
        硬截断：当消息数 <= preserve_recent 但仍超预算时，
        从最早的消息开始丢弃，直到总量在预算内。
        """
        total = sum(len(m.get("content", "")) for m in messages)
        if total <= max_chars:
            return messages

        # 从前向后丢弃，保留尽可能多的最近消息
        result = list(messages)
        dropped = 0
        while len(result) > 1:
            total -= len(result[0].get("content", ""))
            result.pop(0)
            dropped += 1
            if total <= max_chars:
                break

        if dropped > 0:
            result = cls._merge_truncation_marker(
                result,
                f"[{dropped} earlier messages truncated due to length limit]"
            )
        return result

    @classmethod
    def _merge_truncation_marker(cls, messages: List[Dict], marker: str) -> List[Dict]:
        """将截断标记合并到第一条消息，避免破坏角色交替"""
        if not messages:
            return messages

        first = messages[0].copy()
        first["content"] = f"{marker}\n\n{first['content']}"
        return [first] + messages[1:]

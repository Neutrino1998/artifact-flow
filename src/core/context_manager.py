"""
ContextManager — 为每次 LLM 调用构建完整的 messages 列表

职责：
1. 拼接 system prompt（role_prompt + system_time + task_plan + artifacts + agents + tools）
2. 构建对话历史（lead_agent 独有，含 compaction summaries）
3. 构建当前轮事件（按 agent_name 过滤，含 queued_message / tool_complete 等）
4. Token-based 预算截断：基于 LLM 返回的精确 token 数，统一截断 history + tool 消息
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from config import config
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ContextManager:
    """
    为每次 LLM 调用构建完整的 messages 列表。

    纯静态工具类（classmethod only），不持有状态。
    截断策略：字符预算超限时从最旧消息开始丢弃，插入 "[N earlier messages truncated]" 占位。
    """

    @classmethod
    def build(
        cls,
        state: Dict[str, Any],
        agent_name: str,
        agents: Dict[str, Any],  # {name: AgentConfig}
        tools: Dict[str, Any],   # {name: BaseTool}
        artifacts_inventory: Optional[List[Dict]] = None,
        model: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        构建 LLM 调用所需的完整 messages

        设计文档 §唯一的抽象：ContextManager.build()

        Args:
            state: 执行状态
            agent_name: 当前 agent 名称
            agents: 所有 agent 配置 {name: AgentConfig}
            tools: 所有可用工具 {name: BaseTool}
            artifacts_inventory: 预加载的 artifacts 清单（含完整内容）

        Returns:
            List[Dict]（messages 列表）
        """
        from tools.xml_formatter import generate_tool_instruction

        agent_config = agents[agent_name]

        # ========== System Prompt ==========
        system_parts = []

        # 1. 角色提示词（MD body）
        if agent_config.role_prompt:
            system_parts.append(agent_config.role_prompt)

        # 2. 系统时间
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")
        system_parts.append(f'<system_time>Current time: {current_time}</system_time>')

        # 3. 任务计划（从 artifacts 提取全文注入）
        task_plan = cls._find_task_plan(artifacts_inventory)
        if task_plan:
            system_parts.append(
                f'<team_task_plan version="{task_plan["version"]}" '
                f'type="{task_plan["content_type"]}" '
                f'source="{task_plan.get("source", "agent")}" '
                f'updated="{task_plan["updated_at"]}">\n'
                f'<id>{task_plan["id"]}</id>\n'
                f'<content>\n{task_plan["content"]}\n</content>\n'
                f'</team_task_plan>'
            )

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
        system_message = {"role": "system", "content": system_prompt}
        system_chars = len(system_prompt)

        # 两个 agent 类型共用的输入构建
        tool_messages = cls._build_current_input(state, agent_config)

        # Lead 独有：conversation history（DB Message 层，含 compaction summaries）
        history_messages = []
        if agent_config.name == "lead_agent":
            history_messages = state.get("conversation_history", [])

        # ========== Token-based 预算截断 ==========
        all_messages = history_messages + tool_messages

        last_ai_meta, trailing_user_msgs = cls._find_last_ai_and_trailing(all_messages)
        total = last_ai_meta["input_tokens"] + last_ai_meta["output_tokens"]
        if trailing_user_msgs and model:
            from litellm import token_counter
            total += token_counter(model=model, messages=trailing_user_msgs)

        if total > config.CONTEXT_MAX_TOKENS:
            all_messages = cls.truncate_messages(
                all_messages, config.CONTEXT_MAX_TOKENS,
                preserve_ai_msgs=config.TRUNCATION_PRESERVE_AI_MSGS,
            )

        # 发给 LLM 前剥离 _meta
        return [system_message] + cls._strip_meta(all_messages)

    @classmethod
    def _find_task_plan(cls, artifacts_inventory: Optional[List[Dict]]) -> Optional[Dict]:
        """从 artifacts 清单中查找 task_plan"""
        if not artifacts_inventory:
            return None

        for artifact in artifacts_inventory:
            if artifact.get("id") == "task_plan" and artifact.get("content"):
                return artifact
        return None

    @classmethod
    def _preview_content(cls, content: str) -> str:
        """截断内容为 inventory 预览长度"""
        if len(content) > config.INVENTORY_PREVIEW_LENGTH:
            return content[:config.INVENTORY_PREVIEW_LENGTH] + "..."
        return content

    @classmethod
    def _build_artifacts_inventory(cls, artifacts_inventory: List[Dict]) -> str:
        """构建 artifacts 清单部分（内容截断为预览）"""
        count = len(artifacts_inventory)
        lines = [f'{count} artifact(s) in this session.']
        lines.append('<artifacts_inventory>')
        for artifact in artifacts_inventory:
            source = artifact.get("source", "agent")
            lines.append(
                f'<artifact version="{artifact["version"]}" '
                f'type="{artifact["content_type"]}" '
                f'source="{source}" updated="{artifact["updated_at"]}">'
            )
            lines.append(f'<id>{artifact["id"]}</id>')
            lines.append(f'<title>{artifact["title"]}</title>')
            content = artifact.get("content", "")
            preview = cls._preview_content(content)
            is_truncated = len(content) > config.INVENTORY_PREVIEW_LENGTH
            if is_truncated:
                lines.append(f'<content_preview length="{config.INVENTORY_PREVIEW_LENGTH}">{preview}</content_preview>')
            else:
                lines.append(f'<content>{content}</content>')
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
    def _build_current_input(cls, state: Dict[str, Any], agent_config: Any) -> List[Dict[str, str]]:
        """
        Build raw messages from events (no compression). Both lead and sub.
        """
        events = state.get("events", [])
        return cls._build_tool_interactions(events, agent_config.name)

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
                # LLM 响应 → assistant 消息（附加 _meta token usage）
                content = event.data.get("content", "") if event.data else ""
                if content:
                    msg = {"role": "assistant", "content": content}
                    token_usage = event.data.get("token_usage") if event.data else None
                    if token_usage:
                        msg["_meta"] = {
                            "input_tokens": token_usage.get("input_tokens", 0),
                            "output_tokens": token_usage.get("output_tokens", 0),
                        }
                    interactions.append(msg)

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

    @classmethod
    def truncate_messages(
        cls,
        messages: List[Dict],
        budget: int,
        preserve_ai_msgs: int = 4,
    ) -> List[Dict]:
        """
        Token-based truncation at assistant message boundaries.

        Uses _meta.input_tokens + _meta.output_tokens on each assistant message
        to estimate savings. Cuts at assistant boundaries from left to right until
        total - savings <= budget. Preserves at least preserve_ai_msgs assistant
        messages at the tail.

        Args:
            messages: merged history + tool messages (may contain _meta on assistant msgs)
            budget: max token budget
            preserve_ai_msgs: minimum assistant messages to keep at tail
        """
        if not messages:
            return messages

        # Find the last ai msg to estimate total
        last_ai_meta, trailing = cls._find_last_ai_and_trailing(messages)
        total = last_ai_meta["input_tokens"] + last_ai_meta["output_tokens"]

        if total <= budget:
            return messages

        # Collect assistant boundary indices (index of the assistant msg)
        ai_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
        if len(ai_indices) <= preserve_ai_msgs:
            return messages

        # Scan from left; each ai msg's _meta represents the savings of cutting it and everything before
        cut_point = 0  # cut everything before this index (exclusive)
        dropped_count = 0
        # Only consider cutting up to len(ai_indices) - preserve_ai_msgs
        max_cuttable = len(ai_indices) - preserve_ai_msgs

        for scan_idx in range(max_cuttable):
            ai_idx = ai_indices[scan_idx]
            meta = messages[ai_idx].get("_meta", {})
            savings = meta.get("input_tokens", 0) + meta.get("output_tokens", 0)
            if savings <= 0:
                continue
            cut_point = ai_idx + 1
            dropped_count = cut_point
            if total - savings <= budget:
                break
            total -= savings

        if dropped_count > 0:
            result = messages[cut_point:]
            logger.debug(f"Truncated {dropped_count} messages (token-based)")
            result.insert(0, {
                "role": "user",
                "content": f"[{dropped_count} earlier messages truncated]",
            })
            return result

        return messages

    @classmethod
    def _find_last_ai_and_trailing(
        cls, messages: List[Dict]
    ) -> Tuple[Dict[str, int], List[Dict]]:
        """
        Find the last assistant message's _meta and any trailing user/tool messages after it.

        Returns:
            (last_ai_meta dict with input_tokens/output_tokens, list of trailing non-assistant msgs)
        """
        default_meta = {"input_tokens": 0, "output_tokens": 0}
        last_ai_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant":
                last_ai_idx = i
                break

        if last_ai_idx < 0:
            return default_meta, list(messages)

        meta = messages[last_ai_idx].get("_meta", default_meta)
        trailing = messages[last_ai_idx + 1:]
        return meta, trailing

    @classmethod
    def _strip_meta(cls, messages: List[Dict]) -> List[Dict]:
        """Return a copy of messages with _meta keys removed."""
        result = []
        for msg in messages:
            if "_meta" in msg:
                cleaned = {k: v for k, v in msg.items() if k != "_meta"}
                result.append(cleaned)
            else:
                result.append(msg)
        return result

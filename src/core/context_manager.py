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

from config import config
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

    @classmethod
    def build(
        cls,
        state: Dict[str, Any],
        agent_config: Any,  # AgentConfig
        agents: Dict[str, Any],  # {name: AgentConfig} for building available agents section
        tools: Dict[str, Any],   # {name: BaseTool}
        artifact_manager: Optional[Any] = None,
        artifacts_inventory: Optional[List[Dict]] = None,
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
            artifacts_inventory: 预加载的 artifacts 清单（含完整内容）

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

        # ========== 预算截断：先砍 history，再砍 tool interactions ==========
        total = system_chars + cls._chars(history_messages) + cls._chars(tool_messages)
        if total > config.CONTEXT_MAX_CHARS:
            preserve_recent = config.COMPACTION_PRESERVE_PAIRS * 2
            history_budget = max(config.CONTEXT_MAX_CHARS - system_chars - cls._chars(tool_messages), 0)
            history_messages = cls.truncate_messages(
                history_messages, history_budget, preserve_recent=preserve_recent
            )

            total = system_chars + cls._chars(history_messages) + cls._chars(tool_messages)
            if total > config.CONTEXT_MAX_CHARS:
                tool_budget = max(config.CONTEXT_MAX_CHARS - system_chars - cls._chars(history_messages), 0)
                tool_messages = cls.truncate_messages(
                    tool_messages, tool_budget, preserve_recent=config.TOOL_INTERACTION_PRESERVE
                )

        return Context(messages=[system_message] + history_messages + tool_messages)

    INVENTORY_PREVIEW_LENGTH = 200

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
        if len(content) > cls.INVENTORY_PREVIEW_LENGTH:
            return content[:cls.INVENTORY_PREVIEW_LENGTH] + "..."
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
            is_truncated = len(content) > cls.INVENTORY_PREVIEW_LENGTH
            if is_truncated:
                lines.append(f'<content_preview length="{cls.INVENTORY_PREVIEW_LENGTH}">{preview}</content_preview>')
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

    @staticmethod
    def _chars(messages: List[Dict]) -> int:
        return sum(len(m.get("content", "")) for m in messages)

    @classmethod
    def truncate_messages(
        cls,
        messages: List[Dict],
        budget: int,
        preserve_recent: int = 4,
    ) -> List[Dict]:
        """
        从最旧的消息开始丢弃，直到总字符数 <= budget 或只剩 preserve_recent 条。
        preserve_recent 是硬下限，不会被突破。
        """
        if not messages or cls._chars(messages) <= budget:
            return messages

        result = list(messages)
        total = cls._chars(result)
        dropped = 0

        while len(result) > preserve_recent and total > budget:
            total -= len(result[0].get("content", ""))
            result.pop(0)
            dropped += 1

        if dropped > 0:
            logger.debug(f"Truncated {dropped} messages, {total} chars remaining")
            result.insert(0, {
                "role": "user",
                "content": f"[{dropped} earlier messages truncated]",
            })

        return result

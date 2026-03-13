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
        tool_registry: Any,  # ToolRegistry
        artifact_manager: Optional[Any] = None,
        artifacts_inventory: Optional[List[Dict]] = None,
        request_tools: Optional[Dict[str, Any]] = None,
    ) -> Context:
        """
        构建 LLM 调用所需的完整 messages

        设计文档 §唯一的抽象：ContextManager.build()

        Args:
            state: 执行状态
            agent_config: 当前 agent 的 AgentConfig
            agents: 所有 agent 配置 {name: AgentConfig}
            tool_registry: 工具注册中心
            artifact_manager: ArtifactManager（用于 artifacts 清单）
            artifacts_inventory: 预加载的 artifacts 清单
            request_tools: 请求级工具 {name: BaseTool}，优先于 tool_registry

        Returns:
            Context（含 messages 列表）
        """
        from tools.prompt_generator import ToolPromptGenerator

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

        # 6. 工具说明（先查 request_tools，再查 tool_registry）
        tool_names = list(agent_config.tools.keys())
        tools = []
        for name in tool_names:
            tool = (request_tools or {}).get(name) or tool_registry.get_tool(name)
            if tool:
                tools.append(tool)
        if tools:
            system_parts.append(ToolPromptGenerator.generate_tool_instruction(tools))

        system_prompt = "\n\n".join(s for s in system_parts if s)

        # ========== Messages ==========
        messages = [{"role": "system", "content": system_prompt}]

        # 对话历史（仅 lead_agent）
        if agent_config.name == "lead_agent":
            history = state.get("conversation_history", [])
            if history:
                compressed = cls.compress_messages(
                    history,
                    level="normal",
                    preserve_recent=4  # 偶数：[user, asst, user, asst]
                )
                if len(compressed) < len(history):
                    compressed = cls._merge_truncation_marker(
                        compressed,
                        "_[Earlier conversation truncated]_"
                    )
                messages.extend(compressed)

        # 当前输入 + 工具交互历史
        current_input_messages = cls._build_current_input(state, agent_config)
        messages.extend(current_input_messages)

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
                f'<artifact id="{artifact["id"]}" type="{artifact["content_type"]}" '
                f'title="{artifact["title"]}" version="{artifact["version"]}" '
                f'source="{source}" updated="{artifact["updated_at"]}">'
            )
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
        sub_agents = {n: c for n, c in agents.items() if n != current_agent}
        if not sub_agents:
            return "<note>No sub-agents are currently registered. Work independently.</note>"

        lines = ["<available_subagents>"]
        lines.append("Use the `call_subagent` tool to delegate tasks. Provide clear, specific instructions.\n")

        for name, config in sub_agents.items():
            lines.append(f"**{name}**: {config.description}")
            for cap in config.capabilities:
                lines.append(f"  - {cap}")
            lines.append("")

        lines.append("</available_subagents>")
        return "\n".join(lines)

    @classmethod
    def _build_current_input(cls, state: Dict[str, Any], agent_config: Any) -> List[Dict[str, str]]:
        """
        构建当前输入消息（含 tool 交互和 queued messages）

        按 agent_name 过滤事件构建 context：
        - lead 看 current_task + 自己的工具交互
        - subagent 看完整多轮历史（所有 invocation 的 instruction + tool 交互）
        """
        from tools.prompt_generator import format_result

        messages = []
        agent_name = agent_config.name
        events = state.get("events", [])

        if agent_name == "lead_agent":
            # Lead: 用户输入 + queued messages
            instruction = state["current_task"]
            queued = state.get("queued_messages", [])
            if queued:
                instruction += "\n\n[Additional messages received during execution]\n"
                instruction += "\n".join(queued)

            messages.append({"role": "user", "content": instruction})

            # 过滤 lead 的工具交互事件
            tool_interactions = cls._build_tool_interactions(events, agent_name)

            if tool_interactions:
                compressed = cls.compress_messages(
                    tool_interactions,
                    level="normal",
                    preserve_recent=5  # 奇数：[asst, user, asst, user, asst]
                )
                if len(compressed) < len(tool_interactions):
                    compressed = cls._merge_truncation_marker(
                        compressed,
                        "_[Earlier tool calls truncated]_"
                    )
                messages.extend(compressed)
        else:
            # Subagent: instruction 已作为 subagent_instruction 事件注入事件流
            # 按 agent_name 过滤即可拿到完整多轮历史
            tool_interactions = cls._build_tool_interactions(events, agent_name)

            if tool_interactions:
                compressed = cls.compress_messages(
                    tool_interactions,
                    level="normal",
                    preserve_recent=5
                )
                if len(compressed) < len(tool_interactions):
                    compressed = cls._merge_truncation_marker(
                        compressed,
                        "_[Earlier interactions truncated]_"
                    )
                messages.extend(compressed)

        return messages

    @classmethod
    def _build_tool_interactions(cls, events: List, agent_name: str) -> List[Dict[str, str]]:
        """
        从事件流中构建 tool 交互历史

        按 agent_name 过滤事件，处理三种事件类型：
        - subagent_instruction → user 消息（subagent 的 instruction）
        - llm_complete → assistant 消息
        - tool_complete → user 消息
        """
        from tools.prompt_generator import format_result

        interactions = []
        agent_events = [e for e in events if e.agent_name == agent_name]

        for event in agent_events:
            if event.event_type == "subagent_instruction":
                # Subagent instruction → user 消息
                instruction = event.data.get("instruction", "") if event.data else ""
                if instruction:
                    interactions.append({"role": "user", "content": instruction})

            elif event.event_type == "llm_complete":
                # LLM 响应 → assistant 消息
                content = event.data.get("content", "") if event.data else ""
                if content:
                    interactions.append({"role": "assistant", "content": content})

            elif event.event_type == "tool_complete":
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
    def _merge_truncation_marker(cls, messages: List[Dict], marker: str) -> List[Dict]:
        """将截断标记合并到第一条消息，避免破坏角色交替"""
        if not messages:
            return messages

        first = messages[0].copy()
        first["content"] = f"{marker}\n\n{first['content']}"
        return [first] + messages[1:]

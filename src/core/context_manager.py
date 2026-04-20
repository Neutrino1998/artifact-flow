"""
ContextManager — 为每次 LLM 调用构建完整的 messages 列表

职责：
1. 拼接 system prompt（role_prompt + system_time + task_plan + artifacts + agents + tools）
2. 通过 EventHistory 从 state["events"] 构建历史 messages（含 compaction_summary boundary）

Token 预算的上下文控制由引擎内 compaction 负责（见 compaction_runner.py），
ContextManager 本身不再做任何截断。
"""

from typing import Dict, Any, List, Optional
from datetime import datetime

from config import config
from core.event_history import build_event_history
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ContextManager:
    """
    为每次 LLM 调用构建完整的 messages 列表。

    纯静态工具类（classmethod only），不持有状态。
    历史和当前轮事件统一来自 state["events"]（EventHistory 处理 boundary 扫描 + 过滤）。
    Compaction 由引擎 loop 尾部同步触发（不在 build 内执行）。
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

        # 历史 + 当前轮统一来自 state["events"]，EventHistory 处理 boundary / 过滤
        all_messages = build_event_history(state.get("events", []), agent_name)

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

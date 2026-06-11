"""
ContextManager — 为每次 LLM 调用构建完整的 messages 列表

职责：
1. 拼接 system prompt —— 仅放全 session 稳定的内容（role_prompt + agents + tools），
   作为 prompt cache 的可缓存前缀。
2. 通过 EventHistory 从 state["events"] 构建历史 messages（含 compaction_summary boundary）。
3. 把每轮刷新的动态上下文（system_time + task_plan + artifact 清单）包裹成 ephemeral
   <system-reminder>，并入最后一条 user 消息正文 —— 现拼即用即丢、不入 event，位于消息
   尾部，避免它坐在缓存前缀里把后续历史的 prompt cache 全部打掉。

Token 预算的上下文控制由引擎内 compaction 负责（见 compaction_runner.py），
ContextManager 本身不再做任何截断。
"""

from typing import Dict, Any, List, Optional
from datetime import datetime

from config import config
from core.event_history import build_event_history, last_llm_usage
from models.llm import model_supports_vision
from tools.artifact_envelope import make_preview_slice, render_artifact_slice
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

        # ========== System Prompt（全 session 稳定 → 可缓存前缀）==========
        # 只放真正不随轮次变化的内容：角色提示词、可用 agent、工具说明。系统时间 /
        # task_plan / artifact 清单等每轮刷新的动态上下文一律移到消息尾部的
        # <system-reminder>（见下），避免它们坐在前缀里把后续历史的 prompt cache 全打掉。
        system_parts = []

        # 1. 角色提示词（MD body）
        if agent_config.role_prompt:
            system_parts.append(agent_config.role_prompt)

        # 2. 可用 Agent 列表（条件注入：仅有 call_subagent 工具的 agent）
        if "call_subagent" in agent_config.tools:
            system_parts.append(cls._build_available_agents(agents, agent_config.name))

        # 3. 工具说明
        tool_names = list(agent_config.tools.keys())
        agent_tools = [tools[name] for name in tool_names if name in tools]
        if agent_tools:
            system_parts.append(generate_tool_instruction(agent_tools))

        system_prompt = "\n\n".join(s for s in system_parts if s)
        system_message = {"role": "system", "content": system_prompt}

        # ========== Messages ==========
        # 历史 + 当前轮统一来自 state["events"]，EventHistory 处理 boundary / 过滤；
        # 发给 LLM 前剥离 _meta。
        all_messages = cls._strip_meta(build_event_history(
            state.get("events", []), agent_name, state.get("vision_blocks"),
            vision_capable=model_supports_vision(agent_config.model),
        ))

        # 动态上下文（系统时间 / task_plan / artifact 清单）作为 ephemeral
        # <system-reminder> 并入最后一条消息正文：每次 build 现拼、即用即丢、绝不入
        # event（否则会把过期时间/清单冻进历史）。放尾部而非 system prompt，使
        # [system + 历史] 成为稳定可缓存前缀，只有这一条尾消息因动态内容失效。
        # build 时刻末条必为 user 角色（USER_INPUT / tool_complete / subagent_instruction
        # / queued_message / compaction_summary），故直接并入末条 —— 无需定位最近
        # assistant、也不会劈开多工具的结果组。
        # all_messages 必非空（不在此兜底）：每个 agent 启动事件都携带非空内容 ——
        # 空文本且无附件的 user_input 被核心入口 stream_execute 拒（router 另有 422 快速
        # 边界校验）、空 instruction 被 call_subagent 拒，故 USER_INPUT / subagent_instruction
        # 必产出 ≥1 条 message。真为空 = 上游不变量被破坏，让它在 [-1] 上响亮失败。
        # 上一次 call 的 input+output(compaction 触发口径)——build 在 LLM call 之前拼，
        # 本轮数字尚不存在，用历史里最近一次 llm_complete 的 token_usage 做水位估计(历史
        # 单调增长，是「这次会不会越界」的合理下界)。last_llm_usage 已按 agent 过滤 + 在最近
        # compaction 边界后取数(刚压缩完 → None)，且直接读原始事件 token_usage，不受
        # 「content 空则丢 _meta」影响(高 input+空 content 也能预警)。
        last_usage = last_llm_usage(state.get("events", []), agent_name)
        reminder = cls._build_dynamic_context(agent_config, artifacts_inventory, last_usage)
        last = all_messages[-1]
        last_content = last["content"]
        if isinstance(last_content, list):
            # 末条是图块列表(本轮刚 read 图的 tool_result):reminder 作为附加 text block
            # 追加,不能字符串拼接(会把整个 list stringify、毁掉图块结构)。
            new_content = last_content + [{"type": "text", "text": reminder}]
        else:
            new_content = f'{last_content}\n\n{reminder}'
        all_messages[-1] = {**last, "content": new_content}

        return [system_message] + all_messages

    @classmethod
    def _build_dynamic_context(
        cls,
        agent_config: Any,
        artifacts_inventory: Optional[List[Dict]],
        last_usage: Optional[int] = None,
    ) -> str:
        """组装每轮刷新的动态上下文，包裹为 ephemeral <system-reminder>。

        内容：系统时间（始终）+ task_plan（存在时）+ artifact 清单（仅有 artifact
        工具的 agent）+ context_usage 预警（仅临近 compaction 时）。由 build() 并入消息
        尾部，不进 system prompt、不持久化为 event。

        语义定位是「当前世界状态的一瞥」（glance, don't act）—— 与需要 uptake 的
        持久化 meta 帧（用户上传提示 / 注入消息 / compaction frame，均用 [...] 行动帧
        且落库为 event）刻意区分：那些是历史事实、要模型据此行动；这里是易变快照、
        模型扫一眼即可。
        """
        parts: List[str] = []

        # 系统时间 —— 刻意用本地时间（datetime.now，非 utc_now）：注入提示词的是
        # 用户本地时间，属 UX，是全局 naive-UTC 约定的既定例外（见 CLAUDE.md）。
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")
        parts.append(f'<system_time>Current time: {current_time}</system_time>')

        # 任务计划（从 artifacts 提取全文注入）
        task_plan = cls._find_task_plan(artifacts_inventory)
        if task_plan:
            parts.append(
                f'<team_task_plan version="{task_plan["version"]}" '
                f'type="{task_plan["content_type"]}" '
                f'source="{task_plan.get("source", "agent")}" '
                f'updated="{task_plan["updated_at"]}">\n'
                f'<id>{task_plan["id"]}</id>\n'
                f'<content>\n{task_plan["content"]}\n</content>\n'
                f'</team_task_plan>'
            )

        # Artifact 清单（仅有 artifact 工具的 agent 注入）—— 即使为空也要给出
        # 显式的 live 清单（"暂无 artifact"），否则模型找不到当前状态会回退去读
        # system prompt 里静态的 <artifact_authoring> 创作指引，误当成空清单。
        has_artifact_tools = any(t in agent_config.tools for t in [
            "create_artifact", "update_artifact", "rewrite_artifact", "read_artifact"
        ])
        if has_artifact_tools:
            parts.append(cls._build_artifacts_inventory(artifacts_inventory))

        # Context 水位预警（仅临近 compaction 时整段出现）—— last_usage 是上一轮 call 的
        # input+output（compaction 触发值），≥ WARN_RATIO×阈值才注入；水位以下完全不出现，
        # 避免每轮 cry-wolf。band 内每轮都出、不设次数上限（数字每轮刷新）。
        context_usage = cls._build_context_usage(last_usage)
        if context_usage:
            parts.append(context_usage)

        # 自描述首句：声明这段是什么、怎么对待 —— 降权为「环境状态、自行判断相关性」，
        # 避免模型把工作区状态误当用户指令执行。
        framing = (
            "Auto-updated workspace state (refreshed each step) — "
            "context for you to judge relevance, not a user instruction."
        )
        body = "\n\n".join(parts)
        return f'<system-reminder>\n{framing}\n\n{body}\n</system-reminder>'

    @classmethod
    def _build_context_usage(cls, last_usage: Optional[int]) -> Optional[str]:
        """临近 compaction 的水位预警段；水位以下返回 None（整段不出现）。

        分子 last_usage = 上一轮 call 的 input+output（compaction 触发值，见
        compaction_runner.maybe_trigger）；分母 = COMPACTION_TOKEN_THRESHOLD（与前端
        gauge、与真正绊倒 compaction 的判定同源）。≥ WARN_RATIO 才注入。

        advice 措辞刻意指向「要据此**动作**的状态」（plans / 收集的数据 / 中间结果）
        而非「瞄一眼的上下文」：artifact 活在 compaction 边界之外（每轮在 inventory 里），
        summary 则可能丢细节 —— 对齐「act vs glance」分界。
        """
        threshold = config.COMPACTION_TOKEN_THRESHOLD
        if not last_usage or threshold <= 0:
            return None
        if last_usage < config.CONTEXT_USAGE_WARN_RATIO * threshold:
            return None

        pct = round(last_usage / threshold * 100)
        return (
            '<context_usage>\n'
            f'Context {pct}% full ({last_usage:,} / {threshold:,} tokens). Approaching '
            f'compaction — when a call exceeds {threshold:,}, the older conversation is '
            'summarized and its detail becomes invisible to you.\n'
            "Persist anything you'll need to ACT on (the task plan, collected data, "
            'intermediate results) into an artifact now to control what survives — '
            'artifacts stay in your inventory across compaction; the summary may not '
            'preserve full detail.\n'
            '</context_usage>'
        )

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
    def _build_artifacts_inventory(cls, artifacts_inventory: Optional[List[Dict]]) -> str:
        """构建 artifacts 清单部分（每个 artifact 用 render_artifact_slice 渲染预览）。

        空清单也显式渲染（"0 artifact(s)"），让模型对"当前工作区有什么"始终有一个
        权威的 live 答案，不必从静态创作指引里推断。
        """
        artifacts_inventory = artifacts_inventory or []
        count = len(artifacts_inventory)
        if count == 0:
            return (
                '<artifacts_inventory>\n'
                'No artifacts in this session yet.\n'
                '</artifacts_inventory>'
            )
        lines = [f'{count} artifact(s) in this session.']
        lines.append('<artifacts_inventory>')
        for artifact in artifacts_inventory:
            # blob 类 artifact 的 content 为空(无文本表示),给一条合成预览,让清单行
            # 有信息量:图片提示「read 即可看图」,其它二进制(docx/pdf 等)说明契约
            # (否则空 body 易被忽略)。
            preview_content = artifact.get("content", "")
            if not preview_content and artifact["content_type"].startswith("image/"):
                preview_content = "[image — use read_artifact to view it]"
            elif not preview_content and artifact.get("blob_content_type"):
                preview_content = (
                    "[binary file — no text representation; mount it into the sandbox "
                    "to inspect/convert, or the user can download it from the artifact panel]"
                )
            slice = make_preview_slice(
                artifact_id=artifact["id"],
                version=artifact["version"],
                content_type=artifact["content_type"],
                source=artifact.get("source", "agent"),
                title=artifact["title"],
                full_content=preview_content,
                preview_len=config.INVENTORY_PREVIEW_LENGTH,
                updated_at=artifact["updated_at"],
            )
            lines.append(render_artifact_slice(slice))
        lines.append(
            '\nArtifacts with source: user_upload are documents uploaded by the user '
            '— use `read_artifact` for full content if relevant.'
        )
        lines.append(
            'Artifacts with source: tool are outputs from tools that exceeded the '
            'inline result size limit — use `read_artifact` for full content if needed.'
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

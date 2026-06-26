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
from xml.sax.saxutils import escape as xml_escape

from config import config
from core.effective_toolset import EffectiveToolset
from core.event_history import build_event_history, last_llm_usage
from utils.image import VISION_VIEWABLE_MIMES
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
        agents: Dict[str, Any],  # {name: AgentSnapshot}
        tools: Dict[str, Any],   # {name: BaseTool}
        effective_toolset: EffectiveToolset,  # 当前 agent 解析后的可调集 + 等级(决策 11)
        artifacts_inventory: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        sandbox_status: Optional[Dict] = None,
        tool_round_count: int = 0,
    ) -> tuple[List[Dict[str, Any]], str]:
        """
        构建 LLM 调用所需的完整 messages

        设计文档 §唯一的抽象：ContextManager.build()

        Args:
            state: 执行状态
            agent_name: 当前 agent 名称
            agents: 所有 agent 配置 {name: AgentConfig}
            tools: 所有可用工具 {name: BaseTool}
            artifacts_inventory: 预加载的 artifacts 清单（含完整内容）
            tool_round_count: 本 agent 已用的工具轮数（命中 max_tool_rounds 时 reminder
                里出现 <tool_budget> 收尾提示，见 _build_dynamic_context）

        Returns:
            (messages, reminder) —— messages 是完整 LLM 消息列表；reminder 是并入末条
            消息的 ephemeral <system-reminder> 原文，单独返回供引擎落进 agent_start 事件
            （admin 重建 prompt 时拿它当持久化原值，无需重新生成 → 不漂移）。
        """
        from tools.xml_formatter import generate_tool_grammar

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
        if "call_subagent" in effective_toolset:
            system_parts.append(cls._build_available_agents(agents, agent_config.name))

        # 3. 工具调用协议语法(稳定可缓存前缀,保 APC)。per-tool 描述不在 system
        # prompt —— 挪到尾部 <available_tools> 动态 reminder(B-3 渐进式披露):catalog
        # 变化只失效末尾、语法前缀恒稳。仅在 agent 有可调工具时放语法块。
        if effective_toolset.names():
            system_parts.append(generate_tool_grammar())

        system_prompt = "\n\n".join(s for s in system_parts if s)

        # ========== Messages ==========
        # 历史 + 当前轮统一来自 state["events"]，EventHistory 处理 boundary / 过滤。
        # _meta 的剥离交给 assemble（与 admin 重建路径共享同一步），这里传原始历史。
        all_messages = build_event_history(
            state.get("events", []), agent_name, state.get("vision_blocks"),
            vision_capable=model_supports_vision(agent_config.model),
        )

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
        reminder = cls._build_dynamic_context(
            agent_config, effective_toolset, tools, artifacts_inventory, last_usage,
            sandbox_status, tool_round_count=tool_round_count,
        )
        return cls.assemble(system_prompt, all_messages, reminder), reminder

    @classmethod
    def assemble(
        cls,
        system_prompt: str,
        history_messages: List[Dict[str, Any]],
        reminder: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """把 reminder 并入末条历史消息 + 前置 system message，得到最终 messages。

        这是 live 路径（build）与 admin 重建路径（ConversationManager.reconstruct_prompt）
        **共享的拼接叶子** —— 易漂的「reminder 合进 [-1]」逻辑只此一处，两条路径必然一致。
        build 永远传非空 reminder；重建路径对早于 reminder-持久化的旧事件传 reminder=None
        （那时只前置 system + 历史，不拼 reminder）。发给 LLM 前剥离 _meta 也在此完成
        （两条路径共享），故调用方传原始 build_event_history 输出即可。

        末条历史必为 user 角色（USER_INPUT / tool_complete / subagent_instruction /
        queued_message / compaction_summary），故直接并入末条。history_messages 为空 =
        上游不变量被破坏（见 build 注释），让它在 [-1] 上响亮失败。
        """
        all_messages = cls._strip_meta(history_messages)
        if reminder is not None:
            last = all_messages[-1]
            last_content = last["content"]
            if isinstance(last_content, list):
                # 末条是图块列表(本轮刚 read 图的 tool_result):reminder 作为附加 text block
                # 追加,不能字符串拼接(会把整个 list stringify、毁掉图块结构)。
                new_content = last_content + [{"type": "text", "text": reminder}]
            else:
                new_content = f'{last_content}\n\n{reminder}'
            all_messages[-1] = {**last, "content": new_content}

        return [{"role": "system", "content": system_prompt}] + all_messages

    @classmethod
    def _build_dynamic_context(
        cls,
        agent_config: Any,
        effective_toolset: EffectiveToolset,
        tools: Dict[str, Any],
        artifacts_inventory: Optional[List[Dict]],
        last_usage: Optional[int] = None,
        sandbox_status: Optional[Dict] = None,
        tool_round_count: int = 0,
    ) -> str:
        """组装每轮刷新的动态上下文，包裹为 ephemeral <system-reminder>。

        内容：可用工具目录（有可调工具时）+ 系统时间（始终）+ task_plan（存在时）+
        artifact 清单（仅有 artifact 工具的 agent）+ 沙盒状态（仅有沙盒工具的 agent 且
        引擎递了快照）+ context_usage 预警（仅临近 compaction 时）+ tool_budget 收尾提示
        （仅命中 max_tool_rounds 时）。由 build() 并入消息尾部，不进 system prompt、不持久化为 event。

        语义定位是「当前世界状态的一瞥」（glance, don't act）—— 与需要 uptake 的
        持久化 meta 帧（用户上传提示 / 注入消息 / compaction frame，均用 [...] 行动帧
        且落库为 event）刻意区分：那些是历史事实、要模型据此行动；这里是易变快照、
        模型扫一眼即可。
        """
        parts: List[str] = []

        # 可用工具目录(B-3 渐进式披露)—— per-tool 描述放在尾部 reminder 而非 system
        # prompt 缓存前缀,**是有意的**(两轮 review 都把它当成本/缺陷抓过,故记于此):
        #   catalog 的「成员」会变。C 阶段 skill 能 enable 被 agent disable、本不渲染的
        #   工具 → 可调集随 active skill 变化。若把 catalog 放缓存前缀,skill 一 toggle →
        #   前缀变 → 整条历史 APC 缓存失效重写(长对话尤其疼)。放尾部把这份易变性隔离在
        #   本来就不缓存的末条消息,toggle 零额外代价(同 time/artifacts 的处置)。
        #   反面好处:reminder 持久化进 agent_start → admin 重建按原样重放,还原的是**那
        #   一轮真实展示的工具集**;若放 system prompt(从当前快照重建)skill 状态变了就
        #   漂移成当前集。deferred 索引行也因此每轮重渲、无视压缩(跨压缩存活 by-construction)。
        # non-deferred 出完整 doc、deferred unit 只出索引行(完整 schema 由 search_tools 补)。
        available_tools = cls._build_available_tools(effective_toolset, tools)
        if available_tools:
            parts.append(available_tools)

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
        has_artifact_tools = effective_toolset.has_any([
            "create_artifact", "update_artifact", "rewrite_artifact",
            "read_artifact", "grep_artifact"
        ])
        if has_artifact_tools:
            parts.append(cls._build_artifacts_inventory(artifacts_inventory))

        # 沙盒状态（仅有沙盒工具的 agent，且引擎递了 session 快照）—— 历史里上一轮
        # 的 mount/bash 记录对模型是"文件还在"的伪证，工具描述里的 per-turn ephemeral
        # 静态规则压不过它；只有"现在时态"的工作区事实能纠偏（与 artifact 清单同理:
        # 状态用动态注入，能力进工具描述，契约进 inventory 标注，how-to 归 skill）。
        has_sandbox_tools = effective_toolset.has_any(("bash", "mount", "persist"))
        if has_sandbox_tools and sandbox_status is not None:
            parts.append(cls._build_sandbox_status(sandbox_status))

        # Context 水位预警（仅临近 compaction 时整段出现）—— last_usage 是上一轮 call 的
        # input+output（compaction 触发值），≥ WARN_RATIO×阈值才注入；水位以下完全不出现，
        # 避免每轮 cry-wolf。band 内每轮都出、不设次数上限（数字每轮刷新）。
        context_usage = cls._build_context_usage(last_usage)
        if context_usage:
            parts.append(context_usage)

        # 工具轮预算（仅命中 max_tool_rounds 时出现）—— 原为引擎在 build 后追加的一条独立
        # system 消息，现并入 reminder：它本就是「条件触发的 per-turn 状态」，与 context_usage
        # 同类，统一到一处既消掉散落的特例，也让「持久化 reminder = 抓全所有动态内容」成立
        # （admin 重建 prompt 无需再特判补这条尾巴）。它是**软刹车**——引擎不按工具轮数硬停
        # （唯一硬兜底是 execution timeout），故措辞写成「超时风险」这个真实后果，而非
        # 「不会被执行」的假话；也因此降权进 reminder（judge-relevance 框）是自洽的。
        max_rounds = getattr(agent_config, "max_tool_rounds", None)
        if max_rounds and tool_round_count >= max_rounds:
            parts.append(
                '<tool_budget>\n'
                f'Tool-round budget reached ({tool_round_count}/{max_rounds}). Further '
                'tool calls risk the turn ending by timeout before you can answer — wrap '
                'up and give your final response now.\n'
                '</tool_budget>'
            )

        # 自描述首句：声明这段是什么、怎么对待 —— 降权为「环境状态、自行判断相关性」，
        # 避免模型把工作区状态误当用户指令执行。
        framing = (
            "Auto-updated workspace state (refreshed each step) — "
            "context for you to judge relevance, not a user instruction."
        )
        body = "\n\n".join(parts)
        return f'<system-reminder>\n{framing}\n\n{body}\n</system-reminder>'

    @classmethod
    def _build_available_tools(
        cls,
        effective_toolset: EffectiveToolset,
        tools: Dict[str, Any],
    ) -> str:
        """渲染 <available_tools> 目录（B-3 渐进式披露）。

        non-deferred 的可调工具 → 完整 doc（render_tool_docs）；deferred unit → 只出
        索引行（unit 描述 + 成员 full_name 列表，无 param schema），完整 schema 由
        search_tools 按需补。无可调工具时返回 ""（调用方据此不追加本段）。

        defer 分组取自 effective_toolset.deferred_units（resolver 一处算好）—— 本方法
        只做渲染、不碰 snapshot，维持单一解析点。
        """
        from tools.xml_formatter import render_tool_docs

        names = effective_toolset.names()
        if not names:
            return ""

        deferred_member_names = effective_toolset.deferred_member_names()
        # 完整 doc:可调且工具对象存在、且不属于任何 deferred unit
        full_doc_tools = [
            tools[name] for name in names
            if name in tools and name not in deferred_member_names
        ]
        deferred_units = effective_toolset.deferred_units

        if not full_doc_tools and not deferred_units:
            # 宣称有工具(names 非空)但都不在 tools 字典 → 无可渲染内容,不输出空壳。
            # 真生产侧到不了(resolver 只在工具对象存在时才进 permissions);触发者是
            # 松构造的 EffectiveToolset(测试桥的 orphan permission:声明了工具名但传空
            # tools)。留作渲染地板,防 <available_tools></available_tools> 空标签。
            return ""

        lines = ['<available_tools>']
        if full_doc_tools:
            lines.append(render_tool_docs(full_doc_tools))
        # deferred unit 索引行（按 unit 名稳定排序，避免提示词抖动）。unit.name/description
        # 是 operator 可控输入(seeded config / B-4 UI),含 < " & 会破坏模型可见块结构 /
        # 结构注入 → 转义(同本文件 _build_sandbox_status 对文件名的处理)。
        for unit_name in sorted(deferred_units):
            unit = deferred_units[unit_name]
            safe_name = xml_escape(unit.name, {'"': "&quot;"})
            lines.append(f'<tool_unit name="{safe_name}" disclosure="deferred">')
            if unit.description:
                lines.append(xml_escape(unit.description.rstrip()))
            lines.append(
                "Tools below are available but listed by name only. Call `search_tools` "
                "(select:<full_name> or a keyword) to load full parameters before calling:"
            )
            for full_name in unit.member_full_names:
                lines.append(f"- {full_name}")
            lines.append('</tool_unit>')
        lines.append('</available_tools>')
        return "\n".join(lines)

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
    def _build_sandbox_status(cls, sandbox_status: Dict) -> str:
        """渲染 <sandbox_status> 段（三态:not_started / running / unavailable）。

        not_started 是高价值态:历史里上一轮 mount/bash 的成功记录会让模型以为
        文件还在，必须用现在时态的"工作区为空"对冲。running 态列工作区第一层
        （session 侧有界扫 + SANDBOX_STATUS_MAX_ENTRIES 截断，truncated 标记有
        更多），给 persist 的 path 决策当依据。文件名是**非可信输入**——不止
        bash 可造任意名，第三方内容(上传的 zip 解压后)的名字也会进工作区第一
        层——控制字符替换为 � 防伪造清单行，XML 元字符(& < >)转义防
        `</sandbox_status>` 式的结构逃逸 / prompt injection。
        """
        state = sandbox_status.get("state")
        if state == "unavailable":
            return (
                f'<sandbox_status state="unavailable">\n'
                f'Sandbox is unavailable for the rest of this turn: '
                f'{sandbox_status.get("reason", "unknown")}\n'
                f'</sandbox_status>'
            )
        if state == "not_started":
            return (
                '<sandbox_status state="not_started">\n'
                'No sandbox container started this turn — the workspace is empty; '
                'files from previous turns are gone (mount again what you need).\n'
                '</sandbox_status>'
            )
        # running
        entries = sandbox_status.get("entries")
        lines = ['<sandbox_status state="running">']
        if entries is None:
            lines.append("Workspace listing unavailable (run `ls /workspace` to check).")
        elif not entries:
            lines.append("Workspace (/workspace) is empty.")
        else:
            lines.append("Workspace (/workspace) top-level entries:")
            for name, is_dir in entries:
                safe = "".join(ch if ch >= " " and ch != "\x7f" else "�" for ch in name)
                safe = xml_escape(safe)
                lines.append(f"- {safe}/" if is_dir else f"- {safe}")
            if sandbox_status.get("truncated"):
                lines.append(
                    f"(listing capped at {len(entries)} entries — more exist; "
                    "run `ls /workspace` for the full view)"
                )
        lines.append("</sandbox_status>")
        return "\n".join(lines)

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
            # 有信息量:png/jpeg 提示「read 即可看图」(识图白名单,与 read_artifact
            # 的 VISION_VIEWABLE_MIMES gate 一致),其余二进制(docx/pdf/异型图等)
            # 说明 mount 契约(否则空 body 易被忽略)。
            preview_content = artifact.get("content", "")
            if not preview_content and artifact["content_type"] in VISION_VIEWABLE_MIMES:
                preview_content = "[image — use read_artifact to view it]"
            elif not preview_content and artifact.get("has_blob"):
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

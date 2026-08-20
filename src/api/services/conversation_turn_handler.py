"""Conversation turn setup, runtime streaming, and sole finalization owner."""

import asyncio
from typing import Awaitable, Callable, Dict, List, Optional, Any, AsyncGenerator, Tuple
from sqlalchemy.exc import IntegrityError

from core.execution.agent_runtime import (
    AgentInvocation,
    AgentRuntime,
    EngineOutcome,
    RuntimeHooks,
    StopReason,
)
from core.execution.engine import EmptyTurnInputError, create_initial_state, turn_has_content
from core.execution.events import StreamEventType
from core.capabilities.skill_guidance import can_access_skill_bundle, render_skill_guidance
from core.execution.native_call_closure import (
    assert_native_calls_closed,
    close_open_native_calls,
    terminal_reason_for_stop,
)
from core.management.conversation_manager import ConversationManager
from core.execution.post_processing import (
    PostProcessState,
    choose_response_for_terminal,
    decide_terminal,
    ensure_terminal,
)
from tools.base import BaseTool
from tools.builtin.artifact_service import ArtifactService
from repositories.base import NotFoundError
from utils.instance import INSTANCE_ID
from utils.logger import get_logger
from utils.time import utc_now

logger = get_logger("ArtifactFlow")

# Sentinel to signal end of event queue
_SENTINEL = object()


def resolve_skill_activation(
    activate_skills: Optional[List[str]],
    visible: Any,  # 支持 `slug in visible` 的容器(EffectiveSkillSet.visible dict)
    parent_active_skills: List[str],
) -> Tuple[List[str], List[str]]:
    """用户按钮激活的 slug 解析（纯函数）—— 两件正交的事:

      - **to_inject** = 勾选的可见 skill,**只请求内去重**(不按 parent 去重)。重勾一个往轮
        已激活的 skill = 重新注入正文(对齐 agent 自调 read_skill 每次都返回正文,补"正文被
        压缩掉后想重提醒"的缺口)。
      - **active_skills** = parent ∪ to_inject 去重(sticky 名单不堆重复;能力 grant 幂等)。

    可见性 gate 在此(不可见静默丢,不 404 免泄露);空 body gate 另在取正文时(需 DB)。"""
    to_inject: List[str] = []
    for slug in (activate_skills or []):
        if slug in visible and slug not in to_inject:
            to_inject.append(slug)
    active_skills = list(parent_active_skills)
    for slug in to_inject:
        if slug not in active_skills:
            active_skills.append(slug)
    return to_inject, active_skills


class ConversationTurnHandler:
    """Own one admitted Conversation turn from setup through finalization."""

    def __init__(
        self,
        agents: Dict[str, Any],           # {name: AgentSnapshot}
        tools: Dict[str, BaseTool],        # {name: BaseTool}
        effective_toolsets: Dict[str, Any],  # {agent_name: EffectiveToolset}，工具能力单一解析点
        hooks: RuntimeHooks,
        artifact_service: Optional[ArtifactService] = None,
        conversation_manager: Optional[ConversationManager] = None,
        message_event_repo: Optional[Any] = None,  # MessageEventRepository
        on_engine_exit: Optional[Callable[[str, str], Awaitable[None]]] = None,
        db_manager: Optional[Any] = None,
        sandbox_session: Optional[Any] = None,  # duck-typed: status_snapshot(动态上下文快照用,
                                                # 生命周期归 conversation_turn_factory + TaskScope cleanup)
        effective_skillset: Optional[Any] = None,  # None = 无 skill 能力
        user_id: Optional[str] = None,  # 当前认证用户；仅供 LLM cache salt 派生
        entry_agent: str = "lead_agent",
    ):
        self.agents = agents
        self.tools = tools
        self.effective_toolsets = effective_toolsets
        self.effective_skillset = effective_skillset
        self.hooks = hooks
        self.artifact_service = artifact_service

        # Persistence has exactly two complete wiring modes:
        #   1. db_manager: every DB touch opens a fresh retrying session;
        #   2. bound repositories: non-Web/manual and focused tests explicitly inject
        #      both collaborators.
        # An incomplete bound mode used to turn event persistence into a silent
        # success. Reject that invalid state at assembly time instead.
        has_bound_conversation = conversation_manager is not None
        has_bound_events = message_event_repo is not None
        if db_manager is None:
            if not has_bound_conversation or not has_bound_events:
                raise ValueError(
                    "ConversationTurnHandler requires db_manager or both "
                    "conversation_manager and message_event_repo"
                )
        elif has_bound_conversation or has_bound_events:
            raise ValueError(
                "ConversationTurnHandler accepts either db_manager or bound persistence "
                "collaborators, not both"
            )

        self.conversation_manager = conversation_manager
        self.message_event_repo = message_event_repo
        self._on_engine_exit = on_engine_exit
        self._db_manager = db_manager
        self.sandbox_session = sandbox_session
        self.user_id = user_id
        self.entry_agent = entry_agent
        self.runtime = AgentRuntime(
            agents=agents,
            tools=tools,
            effective_toolsets=effective_toolsets,
        )
        logger.info("ConversationTurnHandler initialized")

    async def _with_db_retry(self, fn):
        """
        DB 操作重试适配器。

        fn: async (conv_mgr, event_repo) -> result
        有 db_manager 时委托 db_manager.with_retry（fresh session + 瞬断重试）。
        无 db_manager 时使用显式注入的完整 bound persistence 模式（不重试）。

        **fn 必须幂等**(见 db_manager.with_retry 契约):with_retry 失败时从头重跑 fn。
        幂等键在调用前定好、跨重试稳定;写操作把「已存在」当成功(见
        ConversationManager.create / append_message 的撞重吞、batch_create 的稳定 event_id)。
        """
        if self._db_manager is None:
            # Constructor validation makes both collaborators non-None in this mode.
            return await fn(self.conversation_manager, self.message_event_repo)

        from repositories.conversation_repo import ConversationRepository
        from repositories.message_event_repo import MessageEventRepository

        async def _with_session(session):
            conv_mgr = ConversationManager(ConversationRepository(session))
            event_repo = MessageEventRepository(session)
            return await fn(conv_mgr, event_repo)

        return await self._db_manager.with_retry(_with_session)

    async def run(
        self,
        user_input: str,
        conversation_id: str,
        message_id: str,
        parent_message_id: Optional[str],
        uploaded_files: Optional[List[Dict[str, Any]]] = None,
        force_compact: bool = False,
        activate_skills: Optional[List[str]] = None,
        referenced_artifacts: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行接口（新消息）

        Args:
            user_input: 用户消息内容
            conversation_id: 对话ID
            parent_message_id: 父消息ID
            message_id: 消息ID
            uploaded_files: 本轮随消息上传、已转换的文件 [{"filename","content",
                            "content_type","metadata"}, ...]（closure-carry 自 chat 路由，
                            未即时 commit）。execute_loop 在 turn 起点 stage 进 WorkingSet
                            （发 ARTIFACT_CREATED、随 turn 末 flush），并据回填 id 在
                            USER_INPUT 正文追加归属说明。
            referenced_artifacts: 已在准入边界解析的会话历史上传文件
                            [{"id","filename"}, ...]。仅作为本轮重点引用，不限制
                            其他 session artifact 的可见性。

        Yields:
            流式事件字典
        """
        if user_input is None:
            raise ValueError("'user_input' is required for turn execution")
        if not conversation_id:
            raise ValueError("'conversation_id' is required for an admitted turn")
        if not message_id:
            raise ValueError("'message_id' is required for an admitted turn")
        # 不变量下沉到核心入口：空文本且无附件 = 本轮无可处理输入，会让 USER_INPUT 正文
        # 为空 → 被 EventHistory 过滤 → 空 history → ContextManager.build 在 [-1] 崩。
        # 在此（任何 yield / DB 写之前）拒掉，不依赖调用方校验；router 另留 422 作为 HTTP
        # 快速边界。带附件时 execute_loop 会给 USER_INPUT 拼归属串（非空），故仅无附件时要求非空。
        # 顶层快速闸(pre-DB):文本/附件/compact/activate_skills/引用全空 = 真无输入,早拒(免下方
        # DB setup)。这里对 activate_skills 用 **raw** 值(此刻还没解析可见性/body)—— 它是放行
        # 代理,非权威;skill 可能全被过滤成空,那种情况由**解析后**的 turn_has_content 闸收口
        # (见下,#1:raw activate_skills ≠ 会注入内容)。
        if (
            not user_input.strip()
            and not uploaded_files
            and not force_compact
            and not activate_skills
            and not referenced_artifacts
        ):
            raise ValueError(
                "'user_input' must be non-empty when no artifacts are attached"
            )

        # ========== 准备工作 ==========
        # setup 期对话读写也走 _with_db_retry：每次调用各开短 retrying session，
        # 不跨 LLM / authorization 等待持有连接。
        try:
            await self._with_db_retry(
                lambda cm, er: cm.require_owned(conversation_id, self.user_id)
            )
        except NotFoundError:
            logger.info(
                f"Conversation {conversation_id} deleted before turn setup; "
                f"aborting turn {message_id}"
            )
            return

        resolved_parent = parent_message_id

        # 先发送元数据事件 — as early as possible so frontend knows we're alive
        # Only needs message_id, not a persisted row
        yield {
            "type": StreamEventType.METADATA.value,
            "timestamp": utc_now().isoformat(),
            "data": {
                "conversation_id": conversation_id,
                "message_id": message_id,
                # Display-only live preview for observers. The authoritative
                # terminal snapshot remains Message.user_input +
                # Message.metadata_['uploaded_files']; never put file content here.
                "user_input": user_input,
                "uploaded_files": [
                    {"filename": f["filename"]}
                    for f in (uploaded_files or [])
                ],
                "referenced_artifacts": list(referenced_artifacts or []),
            }
        }

        # Path events — load conversation path 上已持久化的全部事件作为 state["events"]
        # 的历史段（is_historical=True）。Compaction 在引擎内部同步触发，不再需要
        # 异步等待或分布式锁。
        if resolved_parent is None:
            path_events = []
        else:
            path_events = await self._with_db_retry(
                lambda cm, er: cm.load_event_history_async(
                    conv_id=conversation_id, to_message_id=resolved_parent
                )
            )

        # Session
        session_id = conversation_id  # session_id = conversation_id

        # 设置 artifact session
        if self.artifact_service:
            self.artifact_service.set_session(session_id)

        # 从父消息 metadata 中恢复权限与按 agent 隔离的渐进状态。
        parent_always_allowed = []
        agent_progressive_state: Dict[str, Dict[str, List[str]]] = {}
        if resolved_parent:
            parent_meta = await self._with_db_retry(
                lambda cm, er: cm.get_message_metadata_async(resolved_parent)
            )
            parent_always_allowed = parent_meta.get("always_allowed_tools", [])
            raw_progressive = parent_meta.get("agent_progressive_state", {})
            if isinstance(raw_progressive, dict):
                for agent_name, value in raw_progressive.items():
                    if not isinstance(value, dict):
                        continue
                    agent_progressive_state[agent_name] = {
                        "active_skills": list(value.get("active_skills", [])),
                        "disclosed_tools": list(value.get("disclosed_tools", [])),
                    }

        # 用户点按钮激活：activate_skills 经 EffectiveSkillSet.visible 校验(可见=正确性,
        # 不要求 enabled —— 显式激活自己关掉的可见 skill 是合法 opt-in;不可见的静默丢弃,不 404
        # 避免泄露存在性)。拆成两件正交的事(对齐 agent 自调 read_skill:正文每次都返回、名单/
        # 能力幂等去重):
        #   ① 注入集 to_inject = 所有勾选的可见 skill(**只请求内去重**,不按 parent 去重)——
        #      重勾一个往轮已激活的 skill = **重新注入正文**,补上"正文被压缩掉后想重提醒"的缺口
        #      (agent 自调 read_skill 本就每次返回正文,此举把按钮对齐到同一自由度)。
        #   ② sticky 名单 active_skills = parent ∪ to_inject 去重(名单不堆重复;能力 grant 幂等)。
        visible = self.effective_skillset.visible if self.effective_skillset else {}
        lead_state = agent_progressive_state.setdefault(
            self.entry_agent, {"active_skills": [], "disclosed_tools": []}
        )
        parent_lead_skills = list(lead_state["active_skills"])
        to_inject, active_skills = resolve_skill_activation(
            activate_skills, visible, parent_lead_skills
        )
        lead_state["active_skills"] = active_skills

        # 能力轴 sticky 跨 turn:在已算好的字典上 merge 预烤 skill_grants(全 agent),与 mid-turn
        # read_skill 同入口。activate_skill 幂等,对全量 active_skills(parent∪注入)跑一遍即可。
        # 工具能力可跨 turn 持有；L3 mount 不行，因为沙盒按 turn 销毁。
        for agent_name, progressive in agent_progressive_state.items():
            ets = self.effective_toolsets.get(agent_name)
            if ets is None:
                continue
            for slug in progressive.get("active_skills", []):
                ets.activate_skill(slug)

        # obs:能力变更审计(info)—— 只记本轮**新**授予能力的 skill(button/sticky-new),
        # 不含 parent 已激活的重放(那非新事件、每轮都有 = 噪音)。无授予=其 allowed-tools 本就可调。
        for slug in to_inject:
            if slug in parent_lead_skills:
                continue
            granted: set = set()
            for ets in self.effective_toolsets.values():
                grant = ets.skill_grants.get(slug)
                if grant is not None:
                    granted.update(grant.permissions)
            logger.info(
                "Skill %r activated via button (message %s); enabled tools: %s",
                slug, message_id, sorted(granted) or "(none)",
            )

        # 注入集正文：短 session 取 skill_md，用与 read_skill 相同的 renderer 按
        # lead 激活后的实际 sandbox 能力补齐 bundle 提醒，再供 engine 注入 USER_INPUT。
        # 空正文 skip(不注入 None);查不到=脏 slug,
        # 静默略过。重勾已激活 → 完整指导重注入(对齐 agent read_skill)。
        activated_skill_bodies: List[Dict[str, Any]] = []
        if to_inject and self._db_manager:
            from repositories.skill_repo import SkillRepository

            async def _load_bodies(session):
                repo = SkillRepository(session)
                out = []
                for slug in to_inject:
                    info = visible.get(slug)
                    if info is None:
                        continue
                    body = await repo.get_skill_md(info.id)
                    if body and body.strip():
                        out.append({
                            "slug": slug,
                            "name": getattr(info, "name", slug),
                            "body": render_skill_guidance(
                                body,
                                has_extra_files=info.has_extra_files,
                                bundle_accessible=can_access_skill_bundle(
                                    self.effective_toolsets.get(self.entry_agent), slug
                                ),
                            ),
                        })
                return out

            activated_skill_bodies = await self._db_manager.with_retry(_load_bodies)

        # 权威空输入闸(#1):activate_skills 是意图,经可见性/空 body 过滤后才知本轮真会不会注入
        # 内容。顶层闸放行 raw activate_skills;这里按**解析后**的 activated_skill_bodies 收口 ——
        # 与上传/compact/引用对齐(都以"真会注入非空"为准)。全空(勾的全不可见 / 空 body,或
        # 啥也没勾)→ 拒,免空 USER_INPUT 击穿 context_manager 的 [-1]。
        if not turn_has_content(
            user_input,
            uploaded_files,
            force_compact,
            activated_skill_bodies,
            referenced_artifacts,
        ):
            raise EmptyTurnInputError(
                "'user_input' resolves to empty content: the activated skill(s) "
                "produced nothing to inject (not visible / empty body)"
            )

        # L1 候选:enabled 可见 skill 先算同一份；ContextManager 再按各 agent 是否
        # 显式拥有 read_skill 决定是否注入 <available_skills>。effective_skillset
        # 缺省(无 skill / 测试)→ 空列表。
        available_skills = (
            [
                {"slug": info.slug, "name": info.name, "description": info.description}
                for info in self.effective_skillset.available_for_l1()
            ]
            if self.effective_skillset
            else []
        )

        # 创建初始状态
        initial_state = create_initial_state(
            task=user_input,
            session_id=session_id,
            message_id=message_id,
            path_events=path_events,
            always_allowed_tools=parent_always_allowed,
            agent_progressive_state=agent_progressive_state,
            activated_skill_bodies=activated_skill_bodies,
            uploaded_files=uploaded_files,
            referenced_artifacts=referenced_artifacts,
            force_compact=force_compact,
            entry_agent=self.entry_agent,
        )

        logger.info(f"Processing new message (streaming) in conversation {conversation_id}")

        # 本轮按钮激活是 Message 的 display-only 输入快照，与 user_input 同时已知、同生共死；
        # 直接随 Message 创建落 metadata，避免终态补写失败后历史 chips 消失。只存已通过
        # 可见性 + 非空正文解析的技能，不把模型自己调用 read_skill 记成用户点击。
        activated_skills = [
            {"slug": skill["slug"], "name": skill["name"]}
            for skill in activated_skill_bodies
        ]

        # 添加消息到 conversation (after all pre-engine setup to avoid orphaned rows on failure)
        try:
            await self._with_db_retry(
                lambda cm, er: cm.append_message(
                    conv_id=conversation_id,
                    message_id=message_id,
                    user_input=user_input,
                    parent_id=resolved_parent,
                    metadata={
                        **(
                            {"activated_skills": activated_skills}
                            if activated_skills else {}
                        ),
                        **(
                            {"referenced_artifacts": referenced_artifacts}
                            if referenced_artifacts else {}
                        ),
                    } or None,
                )
            )
        except NotFoundError:
            # Initial existence check and message INSERT are separate short
            # transactions. A DELETE can land between them; never resurrect it.
            logger.info(
                f"Conversation {conversation_id} deleted during execution setup; "
                f"aborting turn {message_id} before engine start"
            )
            return

        # ========== Agent runtime ==========
        event_queue: asyncio.Queue = asyncio.Queue()
        runtime_outcome: Optional[EngineOutcome] = None
        runtime_started = asyncio.Event()

        async def emit_to_queue(event_dict):
            await event_queue.put(event_dict)

        async def run_runtime() -> None:
            nonlocal runtime_outcome
            runtime_started.set()
            try:
                runtime_outcome = await self.runtime.run(
                    AgentInvocation(
                        state=initial_state,
                        entry_agent=self.entry_agent,
                        user_id=self.user_id,
                        available_skills=available_skills,
                    ),
                    hooks=self.hooks,
                    event_sink=emit_to_queue,
                    artifact_service=self.artifact_service,
                    sandbox_session=self.sandbox_session,
                )
            finally:
                await event_queue.put(_SENTINEL)

        runtime_task = asyncio.create_task(run_runtime())
        external_cancel: Optional[asyncio.CancelledError] = None
        consumer_closed = False

        try:
            await runtime_started.wait()
            while True:
                event = await event_queue.get()
                if event is _SENTINEL:
                    break
                yield event
        except asyncio.CancelledError as cancel_exc:
            # The outer Conversation task lost authority (lease fence/shutdown).
            # Cancel only the child runtime, drain its partial-state outcome, then
            # continue through the same finalization path as every other stop reason.
            external_cancel = cancel_exc
        except GeneratorExit:
            # run_and_push was cancelled while it was forwarding an event, so it
            # explicitly closed this generator.  GeneratorExit lands at the suspended
            # `yield`, not at event_queue.get(); persist the turn without attempting
            # any more SSE yields, then let aclose() return normally.
            consumer_closed = True
        finally:
            if not runtime_task.done():
                runtime_task.cancel()
            while not runtime_task.done():
                try:
                    await asyncio.shield(runtime_task)
                except asyncio.CancelledError as cancel_exc:
                    # A second outer cancel may land while draining.  Shield keeps
                    # the runtime child alive long enough to return its state.
                    external_cancel = external_cancel or cancel_exc
            if runtime_outcome is None:
                # Defensive fallback for cancellation before the child coroutine
                # entered AgentRuntime.run.  The runtime_started barrier makes this
                # unreachable in normal execution, but finalization still fails safe.
                initial_state["stop_reason"] = StopReason.EXTERNAL_CANCEL
                runtime_outcome = EngineOutcome(
                    state=initial_state,
                    stop_reason=StopReason.EXTERNAL_CANCEL,
                )

        final_state = runtime_outcome.state

        # ========== Post-processing (with late-cancel boundary) ==========
        # Outer cancel (lease fencing / shutdown) can land in any of the awaits below
        # — _on_engine_exit, exists_async, flush_all, _persist_events, update_response,
        # update_metadata. The pp ledger records phase-by-phase progress so the
        # late-cancel recovery (_recover_from_late_cancel) doesn't reconstruct state
        # from scratch — it reads what already completed and continues from there.
        #
        # pp is created BEFORE the first await so late-cancel always has a ledger
        # to operate on. The only way pp stays None is if cancel lands between
        # `pp = PostProcessState(...)` construction and the next await — impossible
        # because there's no await in between.
        pp: Optional[PostProcessState] = None
        try:
            pp = PostProcessState(
                conversation_id=conversation_id,
                message_id=message_id,
                final_state=final_state,
                stop_reason=runtime_outcome.stop_reason,
            )

            # Engine 已退出，不会再 drain 消息 — 立即取消活跃映射，
            # 使 /inject 端点正确返回 409 而非假装成功入队
            if self._on_engine_exit:
                await self._on_engine_exit(conversation_id, message_id)

            # Layer 1: 早判 conversation 是否仍存在。
            # 用户会话 DELETE 已用 execution lease 互斥，但管理员硬删用户的
            # CASCADE / 库外删除仍可以让 conv 行在 engine 跑完前消失。早返回
            # 跳过后续三段写库，避免撞 FK。
            try:
                pp.conv_alive = await self._with_db_retry(
                    lambda cm, er: cm.exists_async(conversation_id)
                )
            except Exception as exists_err:
                # exists 探测的瞬断不应阻塞 post-processing —— 当作 alive 走原流程。
                # 用 exception 落堆栈:这里能藏真 bug(DB 连接/查询逻辑错),不只是瞬断。
                logger.exception(
                    f"exists() probe failed for {conversation_id} (msg={message_id}), "
                    f"falling through to normal post-processing: {exists_err}"
                )
                pp.conv_alive = True

            if not pp.conv_alive:
                logger.info(
                    f"Conversation {conversation_id} deleted during execution, "
                    f"skip persistence (message_id={message_id})"
                )
                # Lease 由 TaskScope 的最外层 finally 兜底释放
                return

            try:
                # Flush dirty artifacts to DB
                if self.artifact_service:
                    try:
                        await self.artifact_service.flush_all(session_id)
                        pp.artifact_flush_completed = True
                    except IntegrityError as flush_ie:
                        # Layer 2: exists() 之后到 flush 之间 conv 被删（TOCTOU）
                        logger.warning(
                            f"Conversation {conversation_id} deleted mid-persist "
                            f"(artifact phase, msg={message_id}): {flush_ie}"
                        )
                        return
                    except Exception as flush_err:
                        logger.exception(f"Artifact flush failed after retries: {flush_err}")
                        pp.flush_error = f"Artifact persistence failed: {flush_err}"
                        pp.artifact_flush_completed = True
                else:
                    pp.artifact_flush_completed = True

                closure_events = close_open_native_calls(
                    pp.final_state, terminal_reason_for_stop(pp.stop_reason)
                )
                for closure_event in closure_events:
                    if not consumer_closed:
                        yield {
                            "type": closure_event.event_type,
                            "agent": closure_event.agent_name,
                            "timestamp": utc_now().isoformat(),
                            "data": closure_event.data,
                        }

                # 决定 terminal（纯函数,无 IO）。统一后 engine/handler 的内部错误只把
                # 详情记进 state["error_detail"],由 decide_terminal 在 flush 之后构建唯一的
                # 终态事件(含 ERROR),handler 下面统一 append + yield。
                decide_terminal(pp)

                if pp.terminal_event is not None and not pp.terminal_appended:
                    pp.final_state["events"].append(pp.terminal_event)
                    pp.terminal_appended = True

                # 持久化事件 —— 必须先于 Message.response 更新。
                # 新架构下 events 是历史 source of truth，持久化失败=下一轮恢复不了本轮。
                # 持久化成功后再更新 Message.response，避免出现"显示成功 + 历史丢失"的假成功状态。
                try:
                    pp.events_persisted = await self._persist_events(
                        message_id, pp.final_state
                    )
                except IntegrityError as events_ie:
                    # Layer 2: events 写阶段命中 conv 删除的 TOCTOU
                    logger.warning(
                        f"Conversation {conversation_id} deleted mid-persist "
                        f"(events phase, msg={message_id}): {events_ie}"
                    )
                    return

                if not pp.events_persisted:
                    # 持久化失败 → 整轮判定失败,覆盖终态为 ERROR,跳过 response/metadata 更新
                    logger.error(
                        f"Aborting turn {message_id}: event persistence failed, "
                        f"Message.response will not be updated"
                    )
                    if not consumer_closed:
                        yield {
                            "type": StreamEventType.ERROR.value,
                            "timestamp": utc_now().isoformat(),
                            "data": {
                                "success": False,
                                "conversation_id": conversation_id,
                                "message_id": message_id,
                                "error": "Event persistence failed — turn aborted, please retry",
                                "instance_id": INSTANCE_ID,
                                "execution_metrics": pp.final_state.get("execution_metrics", {}),
                            },
                        }
                    return

                # events 已落库 → 写 Message.response (单一真相源:
                # success path 和 late-cancel handler 都调 choose_response_for_terminal,
                # 不再有"两份计算"。
                response_to_write = choose_response_for_terminal(pp)
                if response_to_write:
                    # CLAIM the slot BEFORE the await — see PostProcessState's
                    # response_update_attempted docstring for the cancel-mid-await
                    # race rationale. If cancel lands while the await is suspended,
                    # the DB may have already committed but Python never reached a
                    # post-await flag. Late handler checks attempted=True and skips.
                    pp.response_update_attempted = True
                    try:
                        await self._with_db_retry(
                            lambda cm, er: cm.update_response_async(
                                conv_id=conversation_id, message_id=message_id,
                                response=response_to_write,
                            )
                        )
                    except Exception as resp_err:
                        # events 已成功 → 历史正确,仅显示可能短暂落后,不把终态转为 ERROR
                        logger.warning(
                            f"Message.response update failed for {message_id} "
                            f"(events already persisted, display may lag): {resp_err}",
                            exc_info=True,
                        )

                metadata_updates = {}
                always_allowed = pp.final_state.get("always_allowed_tools", [])
                if always_allowed:
                    metadata_updates["always_allowed_tools"] = always_allowed
                progressive = pp.final_state.get("agent_progressive_state", {})
                if progressive:
                    metadata_updates["agent_progressive_state"] = progressive
                execution_metrics = pp.final_state.get("execution_metrics", {})
                if execution_metrics:
                    metadata_updates["execution_metrics"] = execution_metrics
                # 本轮上传文件 [{id, filename}] — display-only 快照,供用户气泡在重载/
                # 切分支后渲染附件(LLM 侧的归属在 USER_INPUT 事件里,与此互不依赖)。
                # flush 失败不写:artifact 没落库,气泡不该声称附件存在(staging 失败
                # 路径 uploaded_artifacts 已被 engine 清空,空列表自然跳过)。
                uploaded_files = pp.final_state.get("uploaded_artifacts") or []
                if uploaded_files and not pp.flush_error:
                    metadata_updates["uploaded_files"] = uploaded_files
                if metadata_updates:
                    try:
                        await self._with_db_retry(
                            lambda cm, er: cm.update_message_metadata_async(
                                conv_id=conversation_id, message_id=message_id, metadata=metadata_updates,
                            )
                        )
                    except Exception as meta_err:
                        logger.warning(
                            f"Message.metadata update failed for {message_id}: {meta_err}",
                            exc_info=True,
                        )

                logger.info("Streaming execution completed")

                # 发送终态到 SSE。统一后 ERROR 也由 decide_terminal 构建为 pp.terminal_event,
                # 与 flush_error / cancelled / TIMED_OUT / COMPLETE 走同一路径。
                if pp.terminal_event is not None and not consumer_closed:
                    yield {
                        "type": pp.terminal_event.event_type,
                        "timestamp": utc_now().isoformat(),
                        "data": pp.terminal_event.data,
                    }

            except Exception as e:
                logger.exception(f"Error in post-processing: {e}")
                if not consumer_closed:
                    yield {
                        "type": StreamEventType.ERROR.value,
                        "timestamp": utc_now().isoformat(),
                        "data": {
                            "success": False,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "error": str(e),
                            "instance_id": INSTANCE_ID,
                        }
                    }
        except asyncio.CancelledError as cancel_exc:
            # Late-cancel landed during post-processing. pp ledger has every
            # phase's "did it complete" recorded — _recover_from_late_cancel
            # reads pp and continues, doesn't re-scan or recompute. Run recovery
            # in a shielded child so a second fence/shutdown cancel cannot strand
            # the ledger halfway through events-first finalization.
            if pp is not None:
                recovery_task = asyncio.create_task(
                    self._recover_from_late_cancel(pp)
                )
                while not recovery_task.done():
                    try:
                        await asyncio.shield(recovery_task)
                    except asyncio.CancelledError:
                        continue
                try:
                    await recovery_task
                except asyncio.CancelledError:
                    logger.error(
                        f"Late-cancel recovery task was itself cancelled for {message_id}"
                    )
                except Exception:
                    logger.exception(
                        f"Late-cancel recovery task failed for {message_id}"
                    )
            raise cancel_exc
        finally:
            # The first outer cancel was intentionally consumed to protect audit
            # finalization.  Propagate it only after the ledger path has completed
            # (including early-return delete/FK cases), so TaskSupervisor still sees
            # a cancelled workload and proceeds with LIFO cleanup.
            if external_cancel is not None:
                raise external_cancel

    async def _recover_from_late_cancel(self, pp: PostProcessState) -> None:
        """
        late-cancel handler — idempotent recovery driven by the pp ledger.

        Invariants (enforced structurally via pp + helpers, not via repeated checks):
        1. events 落库前不写 Message.response          (gate: pp.events_persisted)
        2. response slot 一旦 claimed 不再覆盖          (gate: pp.response_update_attempted)
        3. 已有 semantic terminal 不被 late-cancel 改   (ensure_terminal adopt path)
        4. 只有 runtime external cancel 才写 system placeholder (按 stop_reason 区分)
        """
        # Phase 0: cancel may have landed inside ArtifactService.flush_all after
        # its DB commit but before the await returned. flush_all is structurally
        # idempotent (stable artifact/version keys; successful entries are cleared),
        # so retry the unsettled phase before closing calls or persisting events.
        if not pp.artifact_flush_completed:
            if self.artifact_service:
                try:
                    await self.artifact_service.flush_all(
                        pp.final_state["session_id"]
                    )
                    pp.artifact_flush_completed = True
                except IntegrityError as flush_ie:
                    logger.warning(
                        f"Conversation {pp.conversation_id} deleted during late-cancel "
                        f"artifact recovery (msg={pp.message_id}): {flush_ie}"
                    )
                    return
                except Exception as flush_err:
                    logger.exception(
                        f"Late-cancel artifact flush failed for {pp.message_id}: "
                        f"{flush_err}"
                    )
                    pp.flush_error = f"Artifact persistence failed: {flush_err}"
                    pp.artifact_flush_completed = True
            else:
                pp.artifact_flush_completed = True

        # Phase 1: ensure events are in DB
        if not pp.events_persisted:
            close_open_native_calls(
                pp.final_state, terminal_reason_for_stop(pp.stop_reason)
            )
            ensure_terminal(pp)
            try:
                pp.events_persisted = await self._persist_events(
                    pp.message_id, pp.final_state
                )
                if pp.events_persisted:
                    logger.info(
                        f"Late-cancel persist succeeded for {pp.message_id} "
                        f"(cancel hit mid-post-processing)"
                    )
            except Exception as persist_err:
                # Loud-log but never shadow the propagating CancelledError —
                # the supervisor's cleanup needs to see a cancelled task.
                logger.exception(
                    f"Late-cancel persist failed for {pp.message_id}: {persist_err}"
                )
                pp.events_persisted = False

        if not pp.events_persisted:
            logger.error(
                f"Skipping update_response for {pp.message_id}: late-cancel "
                f"persist failed — refusing to create 'cancel-shown-but-"
                f"events-missing' state. UI bubble will be empty; user can "
                f"retry from previous turn."
            )
            return

        # Phase 2: write Message.response when slot not yet claimed
        if pp.response_update_attempted:
            # success path already claimed (or attempted) — defeats cancel-mid-await
            # race where DB committed real response but await raised before post-await
            # flag could be set
            return

        response_to_write = choose_response_for_terminal(pp)
        if not response_to_write:
            return

        pp.response_update_attempted = True
        try:
            await self._with_db_retry(
                lambda cm, er: cm.update_response_async(
                    conv_id=pp.conversation_id, message_id=pp.message_id,
                    response=response_to_write,
                )
            )
        except Exception as resp_err:
            logger.warning(
                f"Late-cancel response update failed for {pp.message_id}: {resp_err}",
                exc_info=True,
            )

    async def _persist_events(self, message_id: str, final_state: Dict[str, Any]) -> bool:
        """
        持久化事件到 MessageEvent 表

        新架构下 events 是历史的 source of truth（Message.response 仅用于显示），
        持久化失败 = 下一轮恢复不了这一轮的上下文。因此返回 bool 让 caller 能据此
        把 terminal 转成 ERROR，而不是静默吞掉。

        Returns:
            True — 成功，或没有本轮新事件
            False — 批量写入重试后仍失败

        Raises:
            IntegrityError — conv 已被删除（caller 应早返回，跳过后续阶段）
        """
        all_events = final_state.get("events", [])
        assert_native_calls_closed(final_state)
        # 只持久化本轮新产生的 events（历史 events 是 turn 开始时从 DB 载入的快照，
        # 已经在 DB 里，不要重复写）
        new_events = [e for e in all_events if not getattr(e, "is_historical", False)]
        if not new_events:
            return True

        # Assign stable event_id for retry idempotency: {message_id}-{seq}
        db_events = [
            {
                "event_id": f"{message_id}-{seq}",
                "message_id": message_id,
                "event_type": e.event_type,
                "agent_name": e.agent_name,
                "data": e.data,
                "created_at": e.created_at,
            }
            for seq, e in enumerate(new_events)
        ]

        try:
            await self._with_db_retry(
                lambda cm, er: er.batch_create(db_events)
            )
            logger.info(f"Persisted {len(db_events)} events for message {message_id}")
            return True
        except IntegrityError:
            # FK 违规通常意味着 conv/message 行已被删除（TOCTOU 窗口）。
            # 透传给 caller 区分"基础设施失败"和"被外部删除"，避免被
            # 当作普通持久化失败而错误地把整轮转 ERROR 给前端。
            raise
        except Exception as e:
            # 事件丢失 = 最该定位的失败:用 exception 落完整堆栈(原先 error 无堆栈)。
            logger.exception(
                f"Event persistence failed after retries for {message_id} "
                f"({len(db_events)} events lost): {e}"
            )
            return False

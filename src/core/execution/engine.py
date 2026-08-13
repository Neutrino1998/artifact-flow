"""执行引擎 — Pi-style agent loop（嵌套串行）

设计文档 §执行引擎设计方向：
- 唯一的抽象是 context 构建
- call_llm → 接受 native tool calls → 串行执行 → repeat（_run_agent，每 agent 一个循环实例）
- call_subagent = 原地递归 await 子 agent 的循环，返回值即 tool_result ——
  同轮 [tool, subagent, tool] 混合调用按模型给出的自然序串行执行；
  整个 turn 单 asyncio task、单活跃 agent，事件序 = 执行序（审计线性不变量）
- Interrupt = asyncio.Event（in-memory await）
- 多工具支持（provider 返回 native call 列表，引擎串行执行）
- Tool limit → 注入 system message 提醒总结
"""

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    NotRequired,
    Optional,
    Tuple,
    TypedDict,
    Union,
)
from datetime import datetime

from config import config
from core.execution.agent_runtime import (
    RuntimeHooks,
    StopReason,
    get_stop_reason,
    stop_execution,
)
from core.execution.events import StreamEventType, ExecutionEvent
from core.execution.context_manager import ContextManager
from core.capabilities.effective_toolset import EffectiveToolset
from core.execution.compaction_runner import CompactionRunner
from core.execution.cancellation import CooperativeCancelled, run_cancellable
from tools.artifact_envelope import make_preview_slice, render_artifact_slice
from tools.base import ArtifactSpec, BaseTool, ToolExecutionContext, ToolPermission, ToolResult
from utils.instance import INSTANCE_ID
from utils.logger import get_logger, get_request_id
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


# ============================================================
# ExecutionMetrics — 请求级可观测性指标
# ============================================================

class TokenUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    # Provider-reported cache reads. Optional preserves the semantic difference
    # between "not reported" and an explicit cache miss (0).
    cached_input_tokens: NotRequired[int]


class ExecutionMetrics(TypedDict):
    started_at: Union[datetime, str]
    completed_at: Union[datetime, str, None]
    total_duration_ms: Optional[int]
    first_input_tokens: int
    last_output_tokens: int
    last_input_tokens: int
    total_token_usage: TokenUsage
    # True when at least one accumulated LLM call omitted cache-token details.
    cached_input_tokens_partial: bool


def create_initial_metrics() -> ExecutionMetrics:
    return {
        "started_at": utc_now(),
        "completed_at": None,
        "total_duration_ms": None,
        "first_input_tokens": 0,
        "last_output_tokens": 0,
        "last_input_tokens": 0,
        "total_token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "cached_input_tokens_partial": False,
    }


def finalize_metrics(metrics: ExecutionMetrics) -> None:
    started_at = metrics["started_at"]
    completed_at = utc_now()
    metrics["total_duration_ms"] = int((completed_at - started_at).total_seconds() * 1000)
    metrics["started_at"] = started_at.isoformat()
    metrics["completed_at"] = completed_at.isoformat()


def accumulate_token_usage(metrics: ExecutionMetrics, usage: dict) -> None:
    """Accumulate token usage into metrics totals."""
    if usage:
        total = metrics["total_token_usage"]
        total["input_tokens"] += usage.get("input_tokens", 0)
        total["output_tokens"] += usage.get("output_tokens", 0)
        total["total_tokens"] += usage.get("total_tokens", 0)
        if "cached_input_tokens" in usage:
            total["cached_input_tokens"] = (
                total.get("cached_input_tokens", 0)
                + usage["cached_input_tokens"]
            )
        else:
            metrics["cached_input_tokens_partial"] = True


# ============================================================
# 执行状态
# ============================================================

def create_initial_state(
    task: str,
    session_id: str,
    message_id: str,
    path_events: Optional[List[Any]] = None,  # List[ExecutionEvent] with is_historical=True
    always_allowed_tools: Optional[List[str]] = None,
    agent_progressive_state: Optional[Dict[str, Dict[str, List[str]]]] = None,
    activated_skill_bodies: Optional[List[Dict[str, Any]]] = None,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
    force_compact: bool = False,
    entry_agent: str = "lead_agent",
) -> Dict[str, Any]:
    """
    创建初始执行状态

    Args:
        task: 当前用户输入（将作为首个 USER_INPUT 事件由 execute_loop 追加）
        session_id: 会话 ID
        message_id: 本轮消息 ID
        path_events: 当前 conversation path 上的历史事件（is_historical=True），
                     作为 state["events"] 的初始内容；执行中新追加的事件 is_historical=False
        always_allowed_tools: 本会话已允许的工具列表
        uploaded_files: 本轮随消息上传、已转换的文件 [{"filename", "content",
                        "content_type", "metadata"}, ...]。execute_loop 在 turn 起点经
                        ArtifactService.create_from_upload stage 进 WorkingSet（发
                        ARTIFACT_CREATED、随 turn 末 flush 落库），并据回填的 id 在
                        USER_INPUT 正文追加归属说明（仅 LLM 可见）。不在 chat 路由即时 commit。
        agent_progressive_state: 按 agent 隔离的 sticky skill/tool 披露状态。
        activated_skill_bodies: 本轮按钮勾选、要注入正文的 skill [{"slug","name","body"}, ...]
                       (turn handler 取自 skill_md,已过可见性/空 body 过滤)。execute_loop 在
                       USER_INPUT 正文注入(仅 LLM 可见,同 force_compact/上传归属路径),让模型即刻
                       看到指令 —— 与模型自调 read_skill 得正文等价,入口是用户按钮。**重勾一个往轮
                       已激活的 skill 会重新注入正文**(对齐 read_skill 每次都返回正文,补压缩后重
                       提醒缺口);名单/能力仍幂等去重(见 ConversationTurnHandler)。
        force_compact: 用户手动触发的一次性压缩。execute_loop 据此在 USER_INPUT 正文注入压缩
                       指令；compaction_runner 在 entry agent 回答后无视阈值强制压缩一次并消费此标志。
        entry_agent: 顶层执行入口；Chat 传 lead_agent，embedded caller 可指定其他 agent。
    """
    return {
        "current_task": task,
        "session_id": session_id,
        "message_id": message_id,
        # None = running; a StopReason value is the invocation's sole
        # control-plane terminal. Execution events remain the durable history.
        "stop_reason": None,
        "current_agent": entry_agent,
        "always_allowed_tools": list(always_allowed_tools) if always_allowed_tools else [],
        "agent_progressive_state": {
            agent: {
                "active_skills": list(value.get("active_skills", [])),
                "disclosed_tools": list(value.get("disclosed_tools", [])),
            }
            for agent, value in (agent_progressive_state or {}).items()
        },
        # 本轮新激活 skill 的正文(仅供 execute_loop 注入 USER_INPUT,不持久化 —— 正文一旦
        # 进 USER_INPUT 事件就随历史带下来,active_skills slug 名单才是 sticky 状态)。
        "activated_skill_bodies": list(activated_skill_bodies) if activated_skill_bodies else [],
        "events": list(path_events) if path_events else [],
        "execution_metrics": create_initial_metrics(),
        "response": "",
        # uploaded_files = 转换后待 stage 的内容;uploaded_artifacts = stage 后回填的
        # [{id, filename}](execute_loop 填充,供 USER_INPUT 归属说明用)。
        "uploaded_files": list(uploaded_files) if uploaded_files else [],
        "uploaded_artifacts": [],
        "force_compact": force_compact,
    }


class EmptyTurnInputError(ValueError):
    """解析后本轮无任何可注入内容 —— client-caused 的预期失败(典型:stale skill picker,
    勾选的 skill 已被删/不可见/空正文)。上层按 4xx 档处理:warning + 原文案放行给用户,
    不走 exception+脱敏(那是服务端 5xx 档待遇)。"""


@dataclass(frozen=True)
class _PreparedToolCall:
    """One accepted native call after protocol and invocation-snapshot checks."""

    call_id: str
    tool_name: str
    params: Dict[str, Any]
    reason: str
    tool: BaseTool


@dataclass(frozen=True)
class _RejectedToolCall:
    """A native call rejected before authorization or execution."""

    call_id: str
    tool_name: str
    params: Dict[str, Any]
    reason: str
    error: str
    availability_reason: Optional[str] = None


@dataclass(frozen=True)
class _ExecutedToolCall:
    """Execution result awaiting shared result persistence and state updates."""

    result: ToolResult
    duration_ms: int
    include_params: bool


def turn_has_content(
    user_input: str,
    uploaded_files: Optional[List[Any]] = None,
    force_compact: bool = False,
    activated_skill_bodies: Optional[List[Any]] = None,
) -> bool:
    """本轮 USER_INPUT 是否会有内容注入 —— 空输入不变量的**单一真相**。

    正文 / 上传归属串 / skill 正文 / 压缩指令,任一非空即有内容(镜像 execute_loop 的
    `parts` 组装)。全空 → 空 USER_INPUT → 被 EventHistory `if content:` 过滤 → 空 history
    → context_manager `[-1]` 崩(见 context_manager 注释的上游不变量)。

    关键:`activated_skill_bodies` 是**解析后**的 skill 正文(经可见性/去重/空 body 三重过滤),
    **不是** raw `activate_skills` 请求 —— 后者是意图、可能全被过滤掉。上传/compact 的判据本
    就"presence ⟺ 注入非空",skill 不是,故此闸只认解析后的 bodies(三者对齐)。"""
    return bool(
        user_input.strip()
        or uploaded_files
        or force_compact
        or activated_skill_bodies
    )


# emit callback type: async (event_dict) -> None
# Execution always runs to completion regardless of SSE client state.
EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]


async def execute_loop(
    state: Dict[str, Any],
    agents: Dict[str, Any],  # {name: AgentSnapshot}
    tools: Dict[str, Any],   # {name: BaseTool}
    effective_toolsets: Dict[str, EffectiveToolset],  # {agent_name: 可调集+等级}，单一解析点
    hooks: RuntimeHooks,
    artifact_service: Optional[Any] = None,
    emit: Optional[EmitFn] = None,
    sandbox_session: Optional[Any] = None,
    available_skills: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[str] = None,
    entry_agent: str = "lead_agent",
) -> Dict[str, Any]:
    """
    Pi-style 扁平 while loop 执行引擎

    Args:
        state: 执行状态（from create_initial_state）
        agents: {name: AgentSnapshot} 字典（DB 快照重建）
        tools: {name: BaseTool} 字典（全局 builtin + DB external + 请求级工具已合并）
        effective_toolsets: {agent_name: EffectiveToolset} —— 每 agent 解析后的可调
            工具集 + 等级的单一解析结果，引擎按 current agent 索引，替代旧
            的 `AgentConfig.tools` 直读

        hooks: RuntimeHooks（check_cancelled / wait_for_interrupt / drain_messages）
        artifact_service: ArtifactService 实例（duck-typed 协作者：set_session /
            list_artifacts / ingest_tool_result / create_from_upload / bind_emit）
        emit: 事件推送回调（推 SSE）
        sandbox_session: SandboxSession 实例（duck-typed:status_snapshot），仅用于
            动态上下文的 <sandbox_status> 快照——生命周期/拆除归 conversation_turn_factory
            + TaskScope cleanup，引擎不管理它。沙盒工具的 freshness 由通用
            ToolExecutionContext.model_invocation_epoch 传递，引擎不解释 sandbox generation
        user_id: 当前认证用户 ID，仅传给 LLM adapter 派生 provider cache salt；
            不写入 prompt / event
        entry_agent: 顶层 Agent 名称；subagent 仍按 call_subagent 原地递归执行。
    Returns:
        最终执行状态
    """
    from models.llm import (
        LLMContextOverflowError,
        astream_with_retry,
        format_messages_for_debug,
        get_compaction_threshold,
        get_litellm_model_id,
        model_replays_reasoning,
    )

    message_id = state["message_id"]
    # 一次成功或失败的 provider invocation 都占一个 turn-local epoch。同一响应的
    # sibling native calls 共用该值；递归 subagent 使用同一计数器，故父调用恢复后
    # 继续执行时仍携带自己的旧 epoch。它只是一项调用期事实，不持久化进业务 state。
    model_invocation_epoch = 0

    async def _is_cancelled() -> bool:
        """零参谓词：协作式 cancel flag（预绑定 message_id）——所有消费点的唯一入口。

        探针失败（Redis 瞬断等）按「未取消」处理（fail-open + warning）：探针是纯
        UX 信号，失灵的最坏后果是取消晚一拍生效（下个 CANCEL_CHECK_INTERVAL 自然
        重试）；store 持续不可用的 fail-closed 兜底在 heartbeat/lease 层（连续失败
        → 外部 task.cancel，Conversation lease fencing）。绝不让探针异常往上穿 —— 否则它落
        在哪个消费点就伪装成哪个消费点的故障（工具被杀且记成工具失败 / 流式期间记
        成 "LLM call failed" / loop 顶记成 turn ERROR）。
        """
        try:
            return await hooks.check_cancelled(message_id)
        except Exception as probe_err:
            logger.warning(
                f"cancel-flag probe failed for {message_id} "
                f"(treated as not-cancelled, retried next tick): {probe_err}"
            )
            return False

    compaction_runner = CompactionRunner(
        agents=agents,
        emit=emit,
        check_cancelled=_is_cancelled,
        user_id=user_id,
        entry_agent=entry_agent,
    )

    # NOTE: the USER_INPUT event (+ uploaded-file attribution + force_compact
    # directive) is built AFTER `_emit` is defined and uploads are staged —— see
    # the "stage uploads + USER_INPUT" block below. It must run after staging so
    # the attribution listing can reference the freshly-assigned upload ids, and
    # after `_emit`/bind so staging can emit ARTIFACT_CREATED. It still lands in
    # state["events"] before the first _build_context (main loop), so ordering
    # vs. LLM context assembly is unchanged.

    # ── closures ──

    async def _emit(event_type: str, agent: Optional[str] = None, data: Any = None, *, sse_only: bool = False) -> None:
        """推送事件。sse_only=True 仅推 SSE 不入内存事件列表（如 llm_chunk）"""
        # 错误事件统一在此戳入 request_id（发起轮 POST 的 req-id，引擎任务继承）
        # 与 instance_id（受理实例，创建时冻结 —— 读边界注入拿到的是 replay 那个
        # 实例，错的），让 live SSE 与持久化/replay 都带可回传定位码 —— replay 经
        # read 边界脱敏后仍保留（sanitize 不覆盖已有定位字段）。
        if event_type == StreamEventType.ERROR.value and isinstance(data, dict):
            if not data.get("request_id"):
                _rid = get_request_id()
                if _rid:
                    data = {**data, "request_id": _rid}
            if not data.get("instance_id"):
                data = {**data, "instance_id": INSTANCE_ID}
        event_dict = {
            "type": event_type,
            "agent": agent,
            "timestamp": utc_now().isoformat(),
            "data": data,
        }

        if not sse_only:
            state["events"].append(ExecutionEvent(
                event_type=event_type,
                agent_name=agent,
                data=data,
            ))

        if emit:
            await emit(event_dict)

    # ── bind artifact-event emit (must precede upload staging so staged uploads
    #    emit ARTIFACT_CREATED) + unbind at loop end in the main-loop finally ──
    _bind_emit = getattr(artifact_service, "bind_emit", None) if artifact_service else None
    if _bind_emit:
        _bind_emit(_emit)

    # ── stage uploads + build USER_INPUT ──
    # Uploaded files are staged into the WorkingSet here (turn start), NOT
    # committed in the chat router. They go through the SAME create path as model
    # artifacts (source=user_upload) → each emits ARTIFACT_CREATED (the only way a
    # cold-start client sees an upload before flush_all) → all flush together at
    # turn end. This is the unified single lifecycle (see artifact-layer plan
    # decision 1 / stage C). _normalize + dedup happen inside create_from_upload.
    if artifact_service is not None and state.get("uploaded_files"):
        stage_session = state["session_id"]
        staged_ids: List[str] = []
        staging_error: Optional[str] = None
        for f in state["uploaded_files"]:
            try:
                ok, _msg, info = await artifact_service.create_from_upload(
                    session_id=stage_session,
                    filename=f["filename"],
                    content=f["content"],
                    content_type=f["content_type"],
                    metadata=f.get("metadata"),
                    blob=f.get("blob"),
                )
            except Exception as e:
                logger.exception(f"Failed to stage upload '{f.get('filename')}': {e}")
                staging_error = f"Failed to attach file '{f.get('filename')}': {e}"
                break
            if ok and info:
                staged_ids.append(info["id"])
                state["uploaded_artifacts"].append(
                    {"id": info["id"], "filename": info["original_filename"]}
                )
            else:
                logger.error(f"Upload staging failed for '{f.get('filename')}': {_msg}")
                staging_error = f"Failed to attach file '{f.get('filename')}': {_msg}"
                break

        if staging_error is not None:
            # Loud, atomic abort. 静默吞掉一个 stage 失败 = 用户附件凭空消失而无任何
            # 信号(违反 loud-failure)。原子性:回滚本轮已 stage 的文件(纯内存,几个
            # dict pop),使 flush_all 一个都不落 → 用户重试时不撞 _N。
            discard = getattr(artifact_service, "discard_staged", None)
            if discard:
                for sid in staged_ids:
                    discard(stage_session, sid)
            state["uploaded_artifacts"] = []
            # record-not-emit:不在此发 ERROR;只记错误详情,turn 末由 decide_terminal
            # 作为唯一终态发射点统一构建 + 发射 ERROR(带 request_id)。
            state["error_detail"] = {
                "error": staging_error,
                "agent": entry_agent,
                "request_id": get_request_id() or None,
            }
            state["response"] = (
                f"Failed to attach file '{f.get('filename')}'. Please check the file and retry."
            )
            stop_execution(state, StopReason.ERROR)
            # 不 early-return:置终态后落到下方统一尾部(主循环因已有 stop_reason
            # 自然跳过 → finally 解绑 emit → finalize_metrics 序列化 datetime metrics)。
            # 下方 USER_INPUT 构建块由 stop_reason gate 跳过(turn 已在 setup 终止)。

    # 3a. 记录用户原始输入为事件（统一 context 构建路径）。USER_INPUT 正文 = 用户原始输入
    # + 本轮**增补**(turn augmentations，仅 LLM 可见，不入 Message.user_input display)。
    # 三类增补 —— 上传归属串 / skill 正文 / 压缩指令 —— 产地各异(上传 stage 后拿 id、skill
    # 由 turn handler 从 DB 取、compact 静态)，但都汇到这一处:塞进 `parts` list、末尾 join 一次
    # (取代过去三段各自 `f"{c}\n\n{x}" if c.strip() else x` 的复制注入)。turn 非空的唯一真相
    # = `bool(parts)`，由 ConversationTurnHandler.run 的权威闸保证 —— 到这里
    # parts 必非空(空 → 空 USER_INPUT → 被 EventHistory 过滤 → 击穿 context_manager 的 [-1])。
    # stop_reason gate: staging 失败已选择 ERROR 终因 —— turn 在 setup 阶段就终止,
    # 不再构建 USER_INPUT。ERROR 仍由 turn finalization 的唯一 dispatcher 产生。
    if get_stop_reason(state) is None:
        _task = state["current_task"]
        parts: List[str] = [_task] if _task.strip() else []

        _uploaded = state.get("uploaded_artifacts") or []
        if _uploaded:
            # 提示词只列 id —— 模型靠 id 识别文档即可；人读的文件名已在 artifacts inventory
            # 的 title 里。uploaded_artifacts 仍保 filename 作 record，不进提示词避免与 title 重复。
            _listing = ", ".join(a["id"] for a in _uploaded)
            parts.append(
                f"[The user attached {len(_uploaded)} file(s) to this message: {_listing}. "
                f"Use read_artifact with the id for full content.]"
            )

        # 用户点按钮激活 skill：注入新激活 skill 的正文（与模型自调 read_skill 等价,入口是用户
        # 按钮）。能力(grants)已由 handler seed active_skills 烤开,这里只让正文可见;正文入
        # USER_INPUT 后随历史带下,故只注本轮新激活的(往轮的早在其当轮 USER_INPUT 里)。
        for s in (state.get("activated_skill_bodies") or []):
            parts.append(
                f'[The user activated the "{s.get("name") or s["slug"]}" skill. '
                f'Follow its instructions below for this request:\n\n{s["body"]}]'
            )

        # 用户手动触发压缩:注入指令,compaction_runner 在 lead 回答后无视阈值强制压缩一次。
        if state.get("force_compact"):
            parts.append(
                "[Note: the conversation history will be compacted into a summary "
                "right after your response.]"
            )

        # USER_INPUT is both the durable history boundary and the first semantic
        # live event.  Route it through the shared emitter so admin monitoring
        # sees the same event immediately that replay receives after persistence.
        await _emit(
            StreamEventType.USER_INPUT.value,
            agent=entry_agent,
            data={"content": "\n\n".join(parts)},
        )

    def _resolve_tool(name: str):
        """从合并后的 tools dict 查找工具"""
        return tools.get(name)

    def _native_tools_for(agent_name: str) -> list[dict]:
        effective = effective_toolsets[agent_name]
        progressive = state.get("agent_progressive_state", {}).get(agent_name, {})
        disclosed = set(progressive.get("disclosed_tools", []))
        deferred = effective.deferred_member_names()
        names = [
            name for name in effective.names()
            if name in tools and (name not in deferred or name in disclosed)
        ]
        return [tools[name].to_native_tool_schema() for name in names]

    async def _build_context(agent_name: str) -> tuple[list, str, list[dict], int]:
        """drain messages → artifacts 清单 → ContextManager.build。

        返回 (messages, reminder, native_tools, compaction_threshold)：reminder 是并入末条消息的
        <system-reminder> 原文，供调用处落进 agent_start 事件（持久化动态上下文，
        admin 据此重建 messages）；native_tools 只在本次调用内使用。
        """
        if agent_name == entry_agent:
            for msg in await hooks.drain_messages(message_id):
                wrapped = (
                    "[The user has injected a message during execution. "
                    "Consider this input and adjust your approach as needed.]\n"
                    + msg
                )
                await _emit(StreamEventType.QUEUED_MESSAGE.value, entry_agent, {"content": wrapped})

        artifacts_inventory = None
        if artifact_service and state.get("session_id"):
            try:
                artifact_service.set_session(state["session_id"])
                artifacts_inventory = await artifact_service.list_artifacts(
                    session_id=state["session_id"],
                    include_content=True,
                )
            except Exception as e:
                logger.exception(f"Failed to get artifacts inventory: {e}")

        sandbox_status = None
        if sandbox_session is not None:
            try:
                # to_thread:快照含 host 侧单层目录枚举(模型可写的树,条目数不可控)
                sandbox_status = await asyncio.to_thread(sandbox_session.status_snapshot)
            except Exception:
                logger.exception("sandbox status snapshot failed")  # 注入缺席即可,不阻断本轮

        compaction_threshold = get_compaction_threshold(agents[agent_name].model)
        messages, reminder = ContextManager.build(
            state=state,
            agent_name=agent_name,
            agents=agents,
            tools=tools,
            effective_toolset=effective_toolsets[agent_name],
            compaction_threshold=compaction_threshold,
            artifacts_inventory=artifacts_inventory,
            model=get_litellm_model_id(agents[agent_name].model),
            sandbox_status=sandbox_status,
            available_skills=available_skills,
        )

        return messages, reminder, _native_tools_for(agent_name), compaction_threshold

    async def _call_llm(
        messages: list,
        agent_name: str,
        model: str,
        native_tools: list[dict],
    ) -> Optional[Tuple[str, Optional[str], dict, list[dict]]]:
        """
        流式调用 LLM，推送 llm_chunk / llm_complete，记录 metrics。

        Returns:
            (response_content, reasoning_content, token_usage, tool_calls) 或 None
            （LLM 出错，state 已设置）
        """
        llm_start_time = utc_now()

        response_content = ""
        reasoning_content = None
        token_usage = {}
        tool_calls: list[dict] = []

        cancelled_mid_stream = False
        llm_kwargs = {"user_id": user_id} if user_id else {}
        llm_stream = astream_with_retry(
            messages, model=model, tools=native_tools, **llm_kwargs
        )
        try:
            last_cancel_check = time.monotonic()
            async for chunk in llm_stream:
                chunk_type = chunk.get("type")

                if chunk_type == "content":
                    response_content += chunk["content"]
                    await _emit(StreamEventType.LLM_CHUNK.value, agent_name, {
                        "content": response_content,
                    }, sse_only=True)

                elif chunk_type == "reasoning":
                    if reasoning_content is None:
                        reasoning_content = ""
                    reasoning_content += chunk["content"]
                    await _emit(StreamEventType.LLM_CHUNK.value, agent_name, {
                        "reasoning_content": reasoning_content,
                    }, sse_only=True)

                elif chunk_type == "tool_call_progress":
                    # UI-only liveness snapshot.  Partial native arguments are
                    # deliberately not exposed or parsed; only accept()'s full
                    # envelope below is allowed to reach execution/history.
                    await _emit(StreamEventType.LLM_CHUNK.value, agent_name, {
                        "tool_call_progress": chunk["tool_call_progress"],
                    }, sse_only=True)

                elif chunk_type == "usage":
                    token_usage = chunk["token_usage"]

                elif chunk_type == "final":
                    if not response_content and chunk.get("content"):
                        response_content = chunk["content"]
                    if not reasoning_content and chunk.get("reasoning_content"):
                        reasoning_content = chunk["reasoning_content"]
                    if not token_usage and chunk.get("token_usage"):
                        token_usage = chunk["token_usage"]
                    tool_calls = chunk.get("tool_calls") or []

                # 流式输出期间轮询 cancel —— 节流到 CANCEL_CHECK_INTERVAL，避免每个
                # chunk 一次 Redis GET。命中则停止消费，把已累积内容当作本次 llm_complete。
                now = time.monotonic()
                if now - last_cancel_check >= config.CANCEL_CHECK_INTERVAL:
                    last_cancel_check = now
                    # 经软化谓词而非 hooks 直连:探针异常在这里穿出会被下面的
                    # except 记成 "LLM call failed" 的 ERROR 终态(伪装故障源)。
                    if await _is_cancelled():
                        cancelled_mid_stream = True
                        break

        except LLMContextOverflowError:
            # Typed, deterministic provider rejection. No llm_complete was accepted;
            # let _run_agent compact this agent's event history and retry this exact
            # logical step once. All other adapter errors keep the terminal path below.
            raise
        except Exception as llm_error:
            logger.exception(f"LLM call failed: {llm_error}")
            if response_content or reasoning_content:
                await _emit(StreamEventType.LLM_COMPLETE.value, agent_name, {
                    "content": response_content,
                    "reasoning_content": reasoning_content,
                    "tool_calls": [],
                    "token_usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                    "model": model,
                    "duration_ms": int(
                        (utc_now() - llm_start_time).total_seconds() * 1000
                    ),
                })
            # record-not-emit:错误详情记入 state,turn 末由 decide_terminal 统一发射 ERROR。
            state["error_detail"] = {
                "error": f"LLM call failed: {str(llm_error)}",
                "agent": agent_name,
                "request_id": get_request_id() or None,
            }
            state["response"] = "Model request failed. Please retry."
            stop_execution(state, StopReason.ERROR)
            return None
        finally:
            # break 退出 async for 不会自动关闭生成器（参考 redis_stream_transport
            # 同款约定）—— 显式 aclose 以立即释放底层 HTTP 连接；正常 return /
            # 异常路径下生成器已终结，aclose 是 no-op。
            await llm_stream.aclose()

        if cancelled_mid_stream:
            # 把已累积的部分内容作为 llm_complete 持久化 —— events 是历史 source of
            # truth，下一轮恢复时模型能看到自己说到一半的内容。流式中途通常还没收到
            # usage chunk，token_usage 置零即可（本轮 metrics 不再补算）。
            llm_duration_ms = int((utc_now() - llm_start_time).total_seconds() * 1000)
            await _emit(StreamEventType.LLM_COMPLETE.value, agent_name, {
                "content": response_content,
                "reasoning_content": reasoning_content,
                "tool_calls": [],
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "model": model,
                "duration_ms": llm_duration_ms,
            })
            stop_execution(state, StopReason.COOPERATIVE_CANCEL)
            # 只有已流出的普通文本才作为 display 快照写入 state["response"]。
            # Native tool-call deltas 在 adapter 内尚未接受，纯 reasoning / TTFT 阶段
            # 取消时 response_content 为空，由 turn finalization 兜底成占位文案。
            if response_content:
                state["response"] = response_content
            logger.info(f"[{agent_name}] LLM stream cancelled mid-flight, partial content persisted")
            return None

        llm_end_time = utc_now()
        llm_duration_ms = int((llm_end_time - llm_start_time).total_seconds() * 1000)

        # Map LiteLLM keys (prompt_tokens/completion_tokens) to unified keys (input_tokens/output_tokens)
        normalized_usage = {
            "input_tokens": token_usage.get("prompt_tokens", 0),
            "output_tokens": token_usage.get("completion_tokens", 0),
            "total_tokens": token_usage.get("total_tokens", 0),
        }
        if "cached_input_tokens" in token_usage:
            normalized_usage["cached_input_tokens"] = token_usage[
                "cached_input_tokens"
            ]

        await _emit(StreamEventType.LLM_COMPLETE.value, agent_name, {
            "content": response_content,
            "reasoning_content": reasoning_content,
            "tool_calls": tool_calls,
            "token_usage": normalized_usage,
            "model": model,
            "duration_ms": llm_duration_ms,
        })

        accumulate_token_usage(state["execution_metrics"], normalized_usage)

        # Track per-turn token metrics for the top-level entry agent.
        if agent_name == entry_agent:
            metrics = state["execution_metrics"]
            if metrics["first_input_tokens"] == 0:
                metrics["first_input_tokens"] = normalized_usage["input_tokens"]
            metrics["last_output_tokens"] = normalized_usage["output_tokens"]
            metrics["last_input_tokens"] = normalized_usage["input_tokens"]

        input_tokens = normalized_usage["input_tokens"]
        output_tokens = normalized_usage["output_tokens"]

        # Log reasoning before content — reasoning happens first semantically。
        # 截断必报原始长度,避免日志里分不出完整短消息和被切掉的长消息。
        if reasoning_content:
            _r_len = len(reasoning_content)
            _r_marker = "" if _r_len <= 500 else f" (truncated, {_r_len} chars total)"
            logger.debug(f"[{agent_name}] Reasoning{_r_marker}:\n{reasoning_content[:500]}")

        _resp_len = len(response_content)
        _resp_marker = "" if _resp_len <= 500 else f", truncated; {_resp_len} chars total"
        logger.debug(
            f"[{agent_name}] LLM Response "
            f"(input: {input_tokens}, output: {output_tokens}{_resp_marker}):\n"
            f"{response_content[:500]}"
        )

        return response_content, reasoning_content, normalized_usage, tool_calls

    async def _handle_permission(
        call_id: str,
        tool_name: str,
        params: dict,
        agent_name: str,
        permission: ToolPermission,
        reason: Optional[str] = None,
    ) -> bool:
        """
        处理权限中断。

        reason 是平台生成的确定性调用说明，透出到 PERMISSION_REQUEST SSE 事件
        和 interrupt data；不从隐藏 reasoning 或业务参数中提取。

        Returns:
            True — approved, False — denied（含超时和客户端断开）
        """
        await _emit(StreamEventType.PERMISSION_REQUEST.value, agent_name, {
            "permission_level": permission.value,
            "call_id": call_id,
            "tool": tool_name,
            "params": params,
            "reason": reason,
        })

        resume_data = await hooks.wait_for_interrupt(message_id, {
            "type": "tool_permission",
            "agent": agent_name,
            "call_id": call_id,
            "tool_name": tool_name,
            "params": params,
            "reason": reason,
            "permission_level": permission.value,
            "message": f"Tool '{tool_name}' requires {permission.value} permission",
        }, config.PERMISSION_TIMEOUT)

        if resume_data is None:
            logger.warning(f"Permission timeout for tool '{tool_name}' after {config.PERMISSION_TIMEOUT}s, treating as denied")
            await _emit(StreamEventType.PERMISSION_RESULT.value, agent_name, {
                "approved": False, "call_id": call_id,
                "tool": tool_name, "reason": "timeout",
            })
            # 与显式 deny 路径一样配对发 TOOL_START + TOOL_COMPLETE：否则超时
            # 这次 tool_call 在 event history 里没有 TOOL_COMPLETE，下一轮模型只看到
            # 自己发过 call、却看不到任何结果，可能原样重发。
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "call_id": call_id, "tool": tool_name, "params": params, "reason": reason,
            })
            await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                "call_id": call_id, "tool": tool_name, "success": False,
                "error": (
                    f"Permission request expired after {config.PERMISSION_TIMEOUT}s "
                    "without user approval. The user may be away or unavailable. "
                    "The tool was not executed, so this is not a tool failure. "
                    "Treat it as denied for this turn. If this tool is still "
                    "necessary to complete the task, ask the user whether they "
                    "want to approve and retry; otherwise continue without it."
                ),
                "duration_ms": 0,
            })
            return False

        is_approved = resume_data.get("approved", False)

        await _emit(StreamEventType.PERMISSION_RESULT.value, agent_name, {
            "approved": is_approved, "call_id": call_id, "tool": tool_name,
        })

        if not is_approved:
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "call_id": call_id, "tool": tool_name, "params": params, "reason": reason,
            })
            await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                "call_id": call_id, "tool": tool_name, "success": False,
                "error": "Permission denied by user. You do not have permission to use this tool.",
                "duration_ms": 0,
            })
            return False

        if resume_data.get("always_allow", False):
            allowed = list(state.get("always_allowed_tools", []))
            if tool_name not in allowed:
                allowed.append(tool_name)
            state["always_allowed_tools"] = allowed
            logger.info(f"Tool '{tool_name}' added to always_allowed_tools")

        return True

    def _render_persisted_result(
        aid: str,
        spec: ArtifactSpec,
        original_metadata: Optional[Dict[str, Any]],
    ) -> ToolResult:
        """落盘成功后回填:把 tool_result 换成 artifact 预览句柄。
        二进制与文本走不同 hint —— 前者引导 mount/下载,后者引导 read_artifact 读全文。
        XOR 下 blob 在场 ⟺ content 空,故按 blob 在场二分即可。"""
        preview_source = spec.content or ""
        if spec.blob is not None:
            hint = (
                f"Binary file ({len(spec.blob)} bytes, {spec.content_type}) saved as "
                f"artifact '{aid}'. Mount it into the sandbox to process, or it is "
                f"available to the user for download."
            )
        else:
            hint = (
                f"Tool output ({len(preview_source)} chars) saved as artifact '{aid}'. "
                f"Use read_artifact(id='{aid}') for full content; "
                f"preview shows first {config.TOOL_PERSIST_PREVIEW_LENGTH} chars."
            )
        slice = make_preview_slice(
            artifact_id=aid,
            version=1,
            content_type=spec.content_type,
            source="tool",
            title=spec.title or aid,
            full_content=preview_source,
            preview_len=config.TOOL_PERSIST_PREVIEW_LENGTH,
            hint=hint,
        )
        return ToolResult(
            success=True,
            data=render_artifact_slice(slice),
            metadata={
                **(original_metadata or {}),
                "persisted_artifact_id": aid,
            },
        )

    async def _persist_tool_spec(tool_name: str, spec: ArtifactSpec):
        """落盘一个 ArtifactSpec,把 service 缺失 / 异常都折成 (False, reason, None)。
        调用方据此统一 loud-fail —— 落盘是必须的那一刻,失败就是失败,不静默降级。"""
        if artifact_service is None or not state.get("session_id"):
            logger.warning(
                f"Cannot persist artifact for '{tool_name}': service or session unavailable"
            )
            return False, "artifact storage is unavailable", None
        try:
            return await artifact_service.ingest_tool_result(
                session_id=state["session_id"], spec=spec, tool_name=tool_name,
            )
        except Exception as e:
            logger.exception(f"ingest_tool_result failed for '{tool_name}': {e}")
            return False, "internal error while storing the result", None

    async def _maybe_persist_tool_result(
        tool_name: str, tool: BaseTool, result: ToolResult
    ) -> ToolResult:
        """工具结果落盘为 artifact、回填预览句柄。**统一心智模型 = 两问**:
        ①「这次落盘是否必须?」——声明式 artifact(``result.artifact``,blob 和/或
        text)永远必须;无名溢出仅当超 ``max_result_size_chars`` 才必须。②「成功了吗?」
        —— **必须且失败 → 一律 loud-fail**(success=False + 可操作原因),service 缺失 /
        异常 / 配额拒绝全折进这条。

        其余 ``return result``(工具本身已失败 / 结果没超阈值 / ``inf`` 关闭落盘)**不是
        fail-open**,而是「本就无需落盘、结果即数据」。溢出落盘失败**绝不**退回超长原文
        —— 落盘机制本就为保护上下文，失败后退回超长原文会重新引入同一风险。
        """
        if not result.success:
            return result  # 工具自身失败 —— 原样透传

        # ① 声明式 artifact(blob 和/或 text):落盘必须 → 失败一律 loud
        spec = result.artifact
        if spec is not None:
            ok, message, aid = await _persist_tool_spec(tool_name, spec)
            if not ok:
                logger.warning(f"Declared artifact not persisted for '{tool_name}': {message}")
                return ToolResult(success=False, error=message, metadata=result.metadata)
            return _render_persisted_result(aid, spec, result.metadata)

        # ② 无名溢出:仅当超阈值才需落盘
        if math.isinf(tool.max_result_size_chars):
            return result
        data = result.data or ""
        if len(data) <= tool.max_result_size_chars:
            return result

        # 超阈值 → 落盘必须;失败也 loud(绝不把超长原文塞回上下文)
        anon_spec = ArtifactSpec(
            content_type="text/plain",
            filename=f"{tool_name}_output.txt",
            title=f"Output of {tool_name}",
            content=data,
        )
        ok, message, aid = await _persist_tool_spec(tool_name, anon_spec)
        if not ok:
            logger.warning(f"Large tool result not persisted for '{tool_name}': {message}")
            return ToolResult(
                success=False,
                error=(
                    f"Tool output ({len(data)} chars) exceeded the inline limit and "
                    f"could not be saved as an artifact: {message}. Reduce the output "
                    f"size (filter or paginate) and retry."
                ),
                metadata=result.metadata,
            )

        persisted = _render_persisted_result(aid, anon_spec, result.metadata)
        persisted.metadata["original_size_chars"] = len(data)
        return persisted

    def _prepare_tool_call(
        tool_call: dict,
        invocation_tool_names: set[str],
        unexposed_tool_reasons: Dict[str, str],
    ) -> Union[_PreparedToolCall, _RejectedToolCall]:
        """Parse and resolve one call against the frozen provider invocation."""
        call_id = tool_call["id"]
        function = tool_call["function"]
        tool_name = function["name"]
        reason = f"模型请求调用 {tool_name}"
        try:
            params = json.loads(function.get("arguments", ""))
            if not isinstance(params, dict):
                raise ValueError("arguments must decode to a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return _RejectedToolCall(
                call_id=call_id,
                tool_name=tool_name,
                params={},
                reason=reason,
                error=f"Invalid native tool arguments: {exc}",
            )

        # Tool availability is frozen to the schemas actually sent for this
        # provider invocation. Earlier sibling calls cannot retroactively expose
        # a deferred or skill-activated tool in the same assistant envelope.
        if tool_name not in invocation_tool_names:
            availability_reason = unexposed_tool_reasons.get(
                tool_name, "unavailable"
            )
            if availability_reason == "deferred":
                error = (
                    f"Tool '{tool_name}' is enabled but its schema was deferred in "
                    "this LLM invocation. Disclose it with search_tools if needed, "
                    "then retry in the next response."
                )
            elif availability_reason == "disabled_activatable":
                error = (
                    f"Tool '{tool_name}' was disabled when this LLM invocation "
                    "began but can be activated by a relevant skill. Activate that "
                    "skill if needed, then retry in the next response."
                )
            elif availability_reason == "disabled":
                error = (
                    f"Tool '{tool_name}' is disabled for this agent and cannot be "
                    "called in the current configuration. Do not retry it."
                )
            else:
                error = (
                    f"Tool '{tool_name}' is unavailable to this agent. Do not retry it."
                )
            return _RejectedToolCall(
                call_id=call_id,
                tool_name=tool_name,
                params=params,
                reason=reason,
                error=error,
                availability_reason=availability_reason,
            )

        tool = _resolve_tool(tool_name)
        if tool is None:
            return _RejectedToolCall(
                call_id=call_id,
                tool_name=tool_name,
                params=params,
                reason=reason,
                error=f"Tool '{tool_name}' not found",
            )
        return _PreparedToolCall(
            call_id=call_id,
            tool_name=tool_name,
            params=params,
            reason=reason,
            tool=tool,
        )

    async def _finalize_rejected_tool_call(
        rejected: _RejectedToolCall,
        agent_name: str,
    ) -> None:
        await _emit(StreamEventType.TOOL_START.value, agent_name, {
            "call_id": rejected.call_id,
            "tool": rejected.tool_name,
            "params": rejected.params,
            "reason": rejected.reason,
        })
        data = {
            "call_id": rejected.call_id,
            "tool": rejected.tool_name,
            "success": False,
            "error": rejected.error,
            "duration_ms": 0,
        }
        if rejected.availability_reason is not None:
            data["availability_reason"] = rejected.availability_reason
        await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, data)

    async def _authorize_tool_call(
        prepared: _PreparedToolCall,
        agent_name: str,
    ) -> bool:
        """Resolve the permission interrupt before any normal tool side effect."""
        # call_subagent has always used its dedicated AUTO execution path.
        if prepared.tool_name == "call_subagent":
            return True
        effective_permission = (
            effective_toolsets[agent_name].level(prepared.tool_name)
            or prepared.tool.permission
        )
        if (
            effective_permission == ToolPermission.CONFIRM
            and prepared.tool_name not in state.get("always_allowed_tools", [])
        ):
            return await _handle_permission(
                prepared.call_id,
                prepared.tool_name,
                prepared.params,
                agent_name,
                effective_permission,
                prepared.reason,
            )
        return True

    async def _execute_prepared_tool_call(
        prepared: _PreparedToolCall,
        agent_name: str,
        invocation_epoch: int,
    ) -> Optional[_ExecutedToolCall]:
        """Execute one authorized call; None means the whole turn terminated."""
        if prepared.tool_name == "call_subagent":
            try:
                validation_result = await prepared.tool(**prepared.params)
            except Exception as exc:
                logger.exception(f"call_subagent execution error: {exc}")
                validation_result = ToolResult(success=False, error=str(exc))

            if not validation_result.success:
                await _emit(StreamEventType.TOOL_START.value, agent_name, {
                    "call_id": prepared.call_id,
                    "tool": prepared.tool_name,
                    "params": prepared.params,
                    "reason": prepared.reason,
                })
                return _ExecutedToolCall(
                    result=ToolResult(
                        success=False,
                        error=validation_result.error or "call_subagent failed",
                        metadata=validation_result.metadata,
                    ),
                    duration_ms=0,
                    include_params=False,
                )

            target_agent = prepared.params["agent_name"]
            instruction = prepared.params["instruction"]
            fresh_start = prepared.params.get("fresh_start", True)
            started_at = utc_now()
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "call_id": prepared.call_id,
                "tool": prepared.tool_name,
                "params": {
                    "agent_name": target_agent,
                    "instruction": instruction,
                    "fresh_start": fresh_start,
                },
                "reason": prepared.reason,
            })
            state["events"].append(ExecutionEvent(
                event_type=StreamEventType.SUBAGENT_INSTRUCTION.value,
                agent_name=target_agent,
                data={"instruction": instruction, "fresh_start": fresh_start},
            ))

            logger.info(f"Delegating to subagent: {target_agent}")
            sub_response = await _run_agent(target_agent)
            # Restore attribution only after a normal recursive return. If the
            # child raises, the outer error remains attributed to that child.
            state["current_agent"] = agent_name
            if sub_response is None:
                # Handler closure supplies exactly one failure result for this
                # in-flight call and every accepted-but-unstarted sibling.
                return None

            duration_ms = int((utc_now() - started_at).total_seconds() * 1000)
            return _ExecutedToolCall(
                result=ToolResult(
                    success=True,
                    data=(
                        f'<subagent_result agent="{target_agent}">'
                        f'\n{sub_response}'
                        f'\n</subagent_result>'
                    ),
                    metadata={"subagent": target_agent},
                ),
                duration_ms=duration_ms,
                include_params=False,
            )

        started_at = utc_now()
        await _emit(StreamEventType.TOOL_START.value, agent_name, {
            "call_id": prepared.call_id,
            "tool": prepared.tool_name,
            "params": prepared.params,
            "reason": prepared.reason,
        })
        try:
            # Context is per-call because tool instances are shared by concurrent
            # turns. Parameter binding also stays inside this per-tool boundary.
            tool_coro = (
                prepared.tool(_context=ToolExecutionContext(
                    agent_name=agent_name,
                    effective_toolset=effective_toolsets[agent_name],
                    tools=tools,
                    model_invocation_epoch=invocation_epoch,
                    disclosed_tools=set(
                        state.get("agent_progressive_state", {})
                        .get(agent_name, {})
                        .get("disclosed_tools", [])
                    ),
                ), **prepared.params)
                if getattr(prepared.tool, "wants_context", False)
                else prepared.tool(**prepared.params)
            )
            result = await run_cancellable(
                tool_coro, _is_cancelled, config.CANCEL_CHECK_INTERVAL
            )
        except CooperativeCancelled:
            logger.info(
                f"Tool '{prepared.tool_name}' interrupted by user cancel mid-flight"
            )
            result = ToolResult(
                success=False,
                error=(
                    "Cancelled by user while the tool was running. "
                    "Side effects may or may not have been applied "
                    "(the operation was already in flight)."
                ),
            )
        except Exception as exc:
            logger.exception(f"Tool '{prepared.tool_name}' execution error: {exc}")
            result = ToolResult(success=False, error=str(exc))
        return _ExecutedToolCall(
            result=result,
            duration_ms=int((utc_now() - started_at).total_seconds() * 1000),
            include_params=True,
        )

    async def _finalize_tool_call(
        prepared: _PreparedToolCall,
        executed: _ExecutedToolCall,
        agent_name: str,
    ) -> None:
        """Persist/normalize a result, update progressive state, then close it."""
        result = await _maybe_persist_tool_result(
            prepared.tool_name, prepared.tool, executed.result
        )

        # Keep image bytes turn-local; only their stable artifact reference enters
        # the event log. EventHistory rehydrates the block during this turn.
        event_metadata = result.metadata or None
        image = event_metadata.get("image") if event_metadata else None
        if isinstance(image, dict) and "data_uri" in image:
            state.setdefault("vision_blocks_by_call", {})[prepared.call_id] = dict(
                image
            )
            event_metadata = {
                **event_metadata,
                "image": {key: value for key, value in image.items() if key != "data_uri"},
            }

        progressive = state.setdefault("agent_progressive_state", {}).setdefault(
            agent_name, {"active_skills": [], "disclosed_tools": []}
        )
        activated = (
            (result.metadata or {}).get("activated_skill") if result.success else None
        )
        if activated:
            active_list = progressive.setdefault("active_skills", [])
            if activated not in active_list:
                active_list.append(activated)
                effective = effective_toolsets[agent_name]
                grant = effective.skill_grants.get(activated)
                granted = set(grant.permissions) if grant is not None else set()
                effective.activate_skill(activated)
                logger.info(
                    "Skill %r activated for %s via read_skill (message %s); enabled tools: %s",
                    activated,
                    agent_name,
                    message_id,
                    sorted(granted) or "(none)",
                )

        disclosed = (
            (result.metadata or {}).get("disclosed_tools", [])
            if result.success else []
        )
        disclosed_list = progressive.setdefault("disclosed_tools", [])
        for full_name in disclosed:
            if full_name not in disclosed_list:
                disclosed_list.append(full_name)

        data = {
            "call_id": prepared.call_id,
            "tool": prepared.tool_name,
            "success": result.success,
            "result_data": result.data if result.success else None,
            "error": result.error if not result.success else None,
            "duration_ms": executed.duration_ms,
        }
        if executed.include_params:
            data["params"] = prepared.params
        # Preserve the established event shape: ordinary tools always include
        # metadata, while call_subagent validation failures do not.
        if executed.include_params or executed.result.success:
            data["metadata"] = event_metadata
        await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, data)

    async def _execute_tools(
        tool_calls: list,
        agent_name: str,
        invocation_tool_names: set[str],
        unexposed_tool_reasons: Dict[str, str],
        invocation_epoch: int,
    ) -> None:
        """Strictly serial prepare → authorize → execute → finalize pipeline."""
        for tool_call in tool_calls:
            if await _check_cancelled():
                break
            prepared = _prepare_tool_call(
                tool_call, invocation_tool_names, unexposed_tool_reasons
            )
            if isinstance(prepared, _RejectedToolCall):
                await _finalize_rejected_tool_call(prepared, agent_name)
                continue
            if not await _authorize_tool_call(prepared, agent_name):
                continue
            executed = await _execute_prepared_tool_call(
                prepared, agent_name, invocation_epoch
            )
            if executed is None:
                break
            await _finalize_tool_call(prepared, executed, agent_name)

    async def _check_cancelled() -> bool:
        # 同走软化谓词:探针异常在 loop 顶/工具间穿出会被 while 外层
        # except Exception 记成 turn ERROR(一次 Redis 抖动杀掉整个 turn)。
        if await _is_cancelled():
            state["response"] = state.get("response", "") or ""
            stop_execution(state, StopReason.COOPERATIVE_CANCEL)
            return True
        return False

    async def _run_agent(agent_name: str) -> Optional[str]:
        """跑单个 agent 的循环，直至它给出无工具调用的最终回复。

        lead 是顶层调用；subagent 由 _execute_tools 的 call_subagent 分支原地
        递归调用（互递归）。深度无代码级上限，由工具面**配置**约束 —— 当前仅
        lead 的 EffectiveToolset 含 call_subagent，故为一层；给 subagent 授予
        call_subagent 即开启更深嵌套（前端 tool_complete 按 name 配对，多层同名
        在飞时需先改配对策略）。整个 turn 仍是单 asyncio task、单活跃 agent：
        事件序 = 执行序，取消/超时/终态管线不感知递归深度。

        Returns:
            该 agent 的最终文本；None = turn 已终止（cancel / error，stop_reason
            已由故障点按 record-not-emit 设好），调用方据此逐层退栈。
        """
        nonlocal model_invocation_epoch

        if agent_name not in agents:
            logger.error(f"Agent '{agent_name}' not found")
            state["response"] = f"Agent '{agent_name}' is unavailable."
            # record-not-emit:turn 末由 decide_terminal 统一发射 ERROR。
            state["error_detail"] = {
                "error": f"Agent '{agent_name}' not found",
                "agent": agent_name,
                "request_id": get_request_id() or None,
            }
            # stop_reason 一并置位：递归调用方的 while 靠它退栈。
            stop_execution(state, StopReason.ERROR)
            return None

        state["current_agent"] = agent_name  # 错误归因 + 外部观察
        # Exactly-once guard for one logical provider invocation. A successful
        # retry resets it, so a later tool round may independently recover from
        # its own overflow; a second consecutive overflow fails loudly.
        overflow_retry_attempted = False

        while get_stop_reason(state) is None:
            if await _check_cancelled():
                return None

            messages, reminder, native_tools, compaction_threshold = await _build_context(agent_name)
            model_invocation_epoch += 1
            invocation_epoch = model_invocation_epoch
            exposed_tool_names = [
                schema["function"]["name"] for schema in native_tools
            ]

            # 冻结这次 provider invocation 生成时的工具可用性。同一响应里
            # 较早的 skill 激活或 deferred 披露即使改变下一轮 schema，也不能
            # 改写模型已经生成 sibling call 时看到的 schema 集。
            effective = effective_toolsets[agent_name]
            invocation_names = set(exposed_tool_names)
            unexposed_tool_reasons = {
                name: "disabled"
                for name in effective.disabled_tool_names
                if name not in invocation_names and name not in effective.permissions
            }
            for name in effective.activatable_tool_names():
                if name not in invocation_names and name not in effective.permissions:
                    unexposed_tool_reasons[name] = "disabled_activatable"
            for name in effective.deferred_member_names():
                if name not in invocation_names:
                    unexposed_tool_reasons[name] = "deferred"

            # agent_start 持久化 messages 重建所需的非历史输入：静态 system_prompt +
            # 动态 reminder，以及体积很小的 model / exposed tool names。历史可由
            # event 流确定性重放；完整 native schemas 仍只属于本次内存调用，
            # 不重复写入事件/SSE。
            await _emit(StreamEventType.AGENT_START.value, agent_name, {
                "agent": agent_name,
                "system_prompt": messages[0]["content"] if messages and messages[0].get("role") == "system" else None,
                "reminder": reminder,
                "model": agents[agent_name].model,
                "exposed_tool_names": exposed_tool_names,
                "replay_reasoning": model_replays_reasoning(agents[agent_name].model),
            })

            # 守卫:format_messages_for_debug 会遍历 messages,识图块列表里若有图(已压成
            # 摘要、不吐 base64,但仍要遍历)——非 DEBUG 时跳过 eager 求值。
            if logger.debug_mode:
                logger.debug(f"[{agent_name}] Messages:\n{format_messages_for_debug(messages)}")

            # 调用 LLM（流式）
            try:
                llm_result = await _call_llm(
                    messages,
                    agent_name,
                    agents[agent_name].model,
                    native_tools,
                )
            except LLMContextOverflowError as overflow_error:
                if overflow_retry_attempted:
                    message = (
                        "LLM context overflow recovery failed after one "
                        f"compact-and-retry attempt: {overflow_error}"
                    )
                    logger.error(f"[{agent_name}] {message}")
                    state["error_detail"] = {
                        "error": message,
                        "agent": agent_name,
                        "request_id": get_request_id() or None,
                    }
                    state["response"] = (
                        "The model context remained too large after compaction. "
                        "Start a new conversation or reduce the input."
                    )
                    stop_execution(state, StopReason.ERROR)
                    return None

                overflow_retry_attempted = True
                logger.warning(
                    f"[{agent_name}] LLM context overflow; compacting history and "
                    f"retrying once: {overflow_error}"
                )
                try:
                    await compaction_runner.compact_for_overflow(state, agent_name)
                except CooperativeCancelled:
                    logger.info(
                        f"Overflow compaction for {agent_name} interrupted by user cancel"
                    )
                    state["response"] = state.get("response", "") or ""
                    stop_execution(state, StopReason.COOPERATIVE_CANCEL)
                    return None
                except Exception as compact_error:
                    logger.exception(
                        f"Overflow compaction failed for {agent_name}: {compact_error}"
                    )
                    message = f"Context overflow recovery failed: {compact_error}"
                    state["error_detail"] = {
                        "error": message,
                        "agent": agent_name,
                        "request_id": get_request_id() or None,
                    }
                    state["response"] = (
                        "Context recovery failed during compaction. Please retry."
                    )
                    stop_execution(state, StopReason.ERROR)
                    return None
                continue

            if llm_result is None:
                return None
            overflow_retry_attempted = False

            response_content, reasoning_content, normalized_usage, tool_calls = llm_result

            # 引擎内 compaction 检查：本次 LLM 调用 input+output 超阈值则立即压缩。
            # 触发点选「每次 LLM call 后」是两点工程选择：
            #   (1) 可移植性 —— 私有部署模型（vllm 等）无独立 token 计数 API，token
            #       用量只能从已完成 call 返回的 usage 取，故触发必须钩在 call 完成
            #       这一点（既无法预测、也无法事后补测）。
            #   (2) 部分压缩 —— 用此 call 的 input_tokens 判断「response 之前的历史」
            #       是否过大并折叠该段；此 call 之后的 tool result / 续答留在 summary
            #       之后，「上一轮在干什么」的在飞状态由 compact_agent 的 Current Work
            #       段 + 边界后的 fresh events 共同承担。force_compact 同此触发点
            #       （不搬到回合末：那样既丢测量点，又会过度折叠本轮的工具工作）。
            # 失败时 maybe_trigger 已经追加了 success=False 的 compaction_summary 占位
            # （配对 compaction_start），这里把 turn 标 ERROR 退出 —— 对齐 _call_llm 的
            # 失败处理路径，避免在已损坏的 context 上继续跑下个工具/LLM。
            try:
                await compaction_runner.maybe_trigger(
                    state=state,
                    agent_name=agent_name,
                    input_tokens=normalized_usage["input_tokens"],
                    output_tokens=normalized_usage["output_tokens"],
                    compaction_threshold=compaction_threshold,
                )
            except CooperativeCancelled:
                # 用户 cancel 落在 compaction LLM 调用期间（原本是最长的盲窗：
                # COMPACTION_TIMEOUT 秒）。maybe_trigger 的 except Exception 已配对
                # 追加 success=False 的 compaction_summary（EventHistory 跳过，无
                # boundary）—— 此处只需路由到 CANCELLED 终态，不能落进下面的
                # ERROR 分支。
                logger.info(
                    f"Compaction for {agent_name} interrupted by user cancel"
                )
                state["response"] = state.get("response", "") or ""
                stop_execution(state, StopReason.COOPERATIVE_CANCEL)
                return None
            except Exception as compact_error:
                logger.error(f"Compaction failed for {agent_name}: {compact_error}")
                # record-not-emit:turn 末由 decide_terminal 统一发射 ERROR。
                state["error_detail"] = {
                    "error": f"Compaction failed: {str(compact_error)}",
                    "agent": agent_name,
                    "request_id": get_request_id() or None,
                }
                state["response"] = "Conversation compaction failed. Please retry."
                stop_execution(state, StopReason.ERROR)
                return None

            if not tool_calls:
                # Lead 无工具调用但队列中有待处理消息 → 不退出，继续循环
                # 这处理了 inject 消息在最后一次 LLM 调用期间到达的情况
                if agent_name == entry_agent:
                    pending = await hooks.drain_messages(message_id)
                    if pending:
                        for msg in pending:
                            wrapped = (
                                "[The user has injected a message during execution. "
                                "Consider this input and adjust your approach as needed.]\n"
                                + msg
                            )
                            await _emit(StreamEventType.QUEUED_MESSAGE.value, entry_agent, {"content": wrapped})
                        continue  # 回到 while loop 顶部，下次 _build_context 会看到新事件

                # 无待处理消息 → 该 agent 正常完成，最终文本即返回值
                # （lead → 顶层收口 stop_reason/response；subagent → call_subagent
                # 分支包成 <subagent_result> tool_complete）
                await _emit(StreamEventType.AGENT_COMPLETE.value, agent_name, {
                    "agent": agent_name,
                    "content": response_content,
                })
                return response_content

            # 串行执行工具（内部可能递归 _run_agent；turn 终止由 while 顶部条件
            # + _check_cancelled 收口）
            await _execute_tools(
                tool_calls,
                agent_name,
                invocation_names,
                unexposed_tool_reasons,
                invocation_epoch,
            )

        return None  # while 因 stop_reason（cancel / 递归内 error）退出

    # ── main loop ──
    # (_emit already bound to artifact_service above, before upload staging;
    #  unbound in the finally below.)

    try:
        final_response = await _run_agent(entry_agent)
        if final_response is not None:
            state["response"] = final_response
            stop_execution(state, StopReason.COMPLETE)
            logger.info(f"Entry agent {entry_agent} completed, execution done")

    except Exception as e:
        logger.exception(f"Execution loop error: {e}")
        # record-not-emit:turn 末由 decide_terminal 统一发射 ERROR。
        state["error_detail"] = {
            "error": str(e),
            "agent": state.get("current_agent"),
            "request_id": get_request_id() or None,
        }
        state["response"] = "Execution failed unexpectedly. Please retry."
        stop_execution(state, StopReason.ERROR)

    finally:
        if _bind_emit:
            _bind_emit(None)

    # 完成 metrics
    finalize_metrics(state["execution_metrics"])

    return state

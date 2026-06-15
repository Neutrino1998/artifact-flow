"""
执行引擎 — Pi-style 扁平 while loop

设计文档 §执行引擎设计方向：
- 唯一的抽象是 context 构建
- call_llm → parse_tool_calls → 串行执行 → route → repeat
- Interrupt = asyncio.Event（in-memory await）
- 多工具支持（parse_tool_calls 返回列表，串行执行）
- Tool limit → 注入 system message 提醒总结
"""

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable, Awaitable, List, Tuple, TypedDict, Union
from datetime import datetime

from config import config
from core.events import StreamEventType, ExecutionEvent
from core.context_manager import ContextManager
from core.compaction_runner import CompactionRunner
from core.cancellation import CooperativeCancelled, run_cancellable
from tools.artifact_envelope import make_preview_slice, render_artifact_slice
from tools.xml_parser import parse_tool_calls
from tools.base import BaseTool, ToolPermission, ToolResult
from utils.logger import get_logger, get_request_id
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


# ============================================================
# EngineHooks — engine 与外部交互的回调接口
# ============================================================

@dataclass
class EngineHooks:
    """Engine 通过 hooks 与 RuntimeStore 交互，避免 core→api/services 层级倒置。"""
    check_cancelled: Callable[[str], Awaitable[bool]]
    wait_for_interrupt: Callable[[str, Dict[str, Any], float], Awaitable[Optional[Dict[str, Any]]]]
    drain_messages: Callable[[str], Awaitable[List[str]]]


# ============================================================
# ExecutionMetrics — 请求级可观测性指标
# ============================================================

class TokenUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ExecutionMetrics(TypedDict):
    started_at: Union[datetime, str]
    completed_at: Union[datetime, str, None]
    total_duration_ms: Optional[int]
    first_input_tokens: int
    last_output_tokens: int
    last_input_tokens: int
    total_token_usage: TokenUsage


def create_initial_metrics() -> ExecutionMetrics:
    return {
        "started_at": utc_now(),
        "completed_at": None,
        "total_duration_ms": None,
        "first_input_tokens": 0,
        "last_output_tokens": 0,
        "last_input_tokens": 0,
        "total_token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
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


# ============================================================
# 执行状态
# ============================================================

def create_initial_state(
    task: str,
    session_id: str,
    message_id: str,
    path_events: Optional[List[Any]] = None,  # List[ExecutionEvent] with is_historical=True
    always_allowed_tools: Optional[List[str]] = None,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
    force_compact: bool = False,
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
        force_compact: 用户手动触发的一次性压缩。execute_loop 据此在 USER_INPUT 正文注入压缩
                       指令；compaction_runner 在 lead 回答后无视阈值强制压缩一次并消费此标志。
    """
    return {
        "current_task": task,
        "session_id": session_id,
        "message_id": message_id,
        "completed": False,
        "error": False,
        "current_agent": "lead_agent",
        "always_allowed_tools": list(always_allowed_tools) if always_allowed_tools else [],
        "events": list(path_events) if path_events else [],
        "execution_metrics": create_initial_metrics(),
        "response": "",
        # uploaded_files = 转换后待 stage 的内容;uploaded_artifacts = stage 后回填的
        # [{id, filename}](execute_loop 填充,供 USER_INPUT 归属说明用)。
        "uploaded_files": list(uploaded_files) if uploaded_files else [],
        "uploaded_artifacts": [],
        "force_compact": force_compact,
    }


# emit callback type: async (event_dict) -> None
# Execution always runs to completion regardless of SSE client state.
EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]


async def execute_loop(
    state: Dict[str, Any],
    agents: Dict[str, Any],  # {name: AgentConfig}
    tools: Dict[str, Any],   # {name: BaseTool}
    hooks: EngineHooks,
    artifact_service: Optional[Any] = None,
    emit: Optional[EmitFn] = None,
    sandbox_session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Pi-style 扁平 while loop 执行引擎

    Args:
        state: 执行状态（from create_initial_state）
        agents: {name: AgentConfig} 字典
        tools: {name: BaseTool} 字典（全局 + 请求级工具已合并）
        hooks: EngineHooks（check_cancelled / wait_for_interrupt / drain_messages）
        artifact_service: ArtifactService 实例（duck-typed 协作者：set_session /
            list_artifacts / persist_tool_result / bind_emit）
        emit: 事件推送回调（推 SSE）
        sandbox_session: SandboxSession 实例（duck-typed:status_snapshot），仅用于
            动态上下文的 <sandbox_status> 快照——生命周期/拆除归 controller_factory
            + runner cleanup，引擎不管理它
    Returns:
        最终执行状态
    """
    from models.llm import astream_with_retry, format_messages_for_debug, get_litellm_model_id

    message_id = state["message_id"]
    tool_round_count: Dict[str, int] = {}  # per-agent tool round counter

    async def _is_cancelled() -> bool:
        """零参谓词：协作式 cancel flag（预绑定 message_id）——所有消费点的唯一入口。

        探针失败（Redis 瞬断等）按「未取消」处理（fail-open + warning）：探针是纯
        UX 信号，失灵的最坏后果是取消晚一拍生效（下个 CANCEL_CHECK_INTERVAL 自然
        重试）；store 持续不可用的 fail-closed 兜底在 heartbeat/lease 层（连续失败
        → 外部 task.cancel，execution_runner）。绝不让探针异常往上穿 —— 否则它落
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
        agents=agents, emit=emit, check_cancelled=_is_cancelled
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
        # 错误事件统一在此戳入 request_id（发起轮 POST 的 req-id，引擎任务继承），
        # 让 live SSE 与持久化/replay 都带可回传定位码 —— replay 经 read 边界脱敏后
        # 仍保留此码（sanitize 不覆盖已有 request_id）。
        if (
            event_type == StreamEventType.ERROR.value
            and isinstance(data, dict)
            and not data.get("request_id")
        ):
            _rid = get_request_id()
            if _rid:
                data = {**data, "request_id": _rid}
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
                    blob_content_type=f.get("blob_content_type"),
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
                "agent": "lead_agent",
                "request_id": get_request_id() or None,
            }
            state["completed"] = True
            state["error"] = True
            # 不 early-return:置 completed 后落到下方统一尾部(主循环因 completed=True
            # 自然跳过 → finally 解绑 emit → finalize_metrics 序列化 datetime metrics)。
            # 下方 USER_INPUT 构建块由 `if not state["completed"]` 跳过(turn 已在 setup 终止)。

    # 3a. 记录用户原始输入为事件（统一 context 构建路径）。本轮随消息上传的 artifact 在
    # 事件正文（仅 LLM 可见，不入 Message.user_input display）追加归属说明，让 agent 把
    # "分析这个"对应到刚传的文件、并知道用 read_artifact 读全文。不改 state["current_task"]
    # （compaction 复用）。
    # `if not completed`: staging 失败已置 completed/error 并 emit 过 ERROR —— turn 在
    # setup 阶段就终止,不再构建 USER_INPUT(否则事件流会变成 [ERROR, USER_INPUT] 的
    # 错序,且把一条本轮根本没跑的用户输入塞进历史)。下方主循环同样因 completed 跳过。
    if not state["completed"]:
        user_input_content = state["current_task"]
        _uploaded = state.get("uploaded_artifacts") or []
        if _uploaded:
            # 提示词只列 id —— 模型靠 id 识别文档即可；人读的文件名已在 artifacts inventory
            # 的 title 里。uploaded_artifacts 仍保 filename 作 record，不进提示词避免与 title 重复。
            _listing = ", ".join(a["id"] for a in _uploaded)
            user_input_content = (
                f"{user_input_content}\n\n"
                f"[The user attached {len(_uploaded)} file(s) to this message: {_listing}. "
                f"Use read_artifact with the id for full content.]"
            )
        # 用户手动触发压缩：在 USER_INPUT 正文注入指令（仅 LLM 可见，同上传归属串路径）。始终
        # 注入 —— 有正文则追加、纯压缩轮次则指令即正文，让 lead 总有可回应的输入。
        if state.get("force_compact"):
            _compact_directive = (
                "[Note: the conversation history will be compacted into a summary "
                "right after your response.]"
            )
            user_input_content = (
                f"{user_input_content}\n\n{_compact_directive}"
                if user_input_content.strip()
                else _compact_directive
            )
        state["events"].append(ExecutionEvent(
            event_type=StreamEventType.USER_INPUT.value,
            agent_name="lead_agent",
            data={"content": user_input_content},
        ))

    def _resolve_tool(name: str):
        """从合并后的 tools dict 查找工具"""
        return tools.get(name)

    async def _build_context(agent_name: str) -> tuple[list, str]:
        """drain messages → artifacts 清单 → ContextManager.build。

        返回 (messages, reminder)：reminder 是并入末条消息的 <system-reminder> 原文，
        供调用处落进 agent_start 事件（持久化动态上下文，admin 据此重建 prompt）。
        """
        if current_agent_name == "lead_agent":
            for msg in await hooks.drain_messages(message_id):
                wrapped = (
                    "[The user has injected a message during execution. "
                    "Consider this input and adjust your approach as needed.]\n"
                    + msg
                )
                await _emit(StreamEventType.QUEUED_MESSAGE.value, "lead_agent", {"content": wrapped})

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

        # max_tool_rounds 收尾提示已并入 reminder（见 ContextManager._build_dynamic_context
        # 的 <tool_budget>）——引擎只把 live 工具轮数传进去，不再在 build 后追加独立 system 消息。
        messages, reminder = ContextManager.build(
            state=state,
            agent_name=agent_name,
            agents=agents,
            tools=tools,
            artifacts_inventory=artifacts_inventory,
            model=get_litellm_model_id(agents[agent_name].model),
            sandbox_status=sandbox_status,
            tool_round_count=tool_round_count.get(agent_name, 0),
        )

        return messages, reminder

    async def _complete_agent(agent_name: str, response_content: str) -> None:
        """
        完成当前 agent，发送 agent_complete 事件。

        - lead 无工具调用 → completed = True
        - subagent 无工具调用 → 切回 lead，追加 call_subagent tool_complete
        """
        await _emit(StreamEventType.AGENT_COMPLETE.value, agent_name, {
            "agent": agent_name,
            "content": response_content,
        })

        if agent_name == "lead_agent":
            state["completed"] = True
            state["response"] = response_content
            logger.info("Lead agent completed, execution done")
        else:
            # Subagent 完成 → 切回 lead
            # subagent 的响应作为 call_subagent 的 tool_result 返回给 lead
            state["current_agent"] = "lead_agent"
            logger.info(f"Subagent {agent_name} completed, switching back to lead_agent")

            subagent_xml = (
                f'<subagent_result agent="{agent_name}">'
                f'\n{response_content}'
                f'\n</subagent_result>'
            )
            await _emit(StreamEventType.TOOL_COMPLETE.value, "lead_agent", {
                "tool": "call_subagent",
                "success": True,
                "result_data": subagent_xml,
                "duration_ms": 0,
                # call_subagent 调用本身的 parser_warnings 在 _execute_tools 切换 agent 时
                # 暂存到 state，这里取回写入 deferred tool_complete。
                "parser_warnings": state.pop("pending_subagent_parser_warnings", None),
            })

    async def _call_llm(messages: list, agent_name: str, model: str) -> Optional[Tuple[str, Optional[str], dict]]:
        """
        流式调用 LLM，推送 llm_chunk / llm_complete，记录 metrics。

        Returns:
            (response_content, reasoning_content, token_usage) 或 None（LLM 出错，state 已设置）
        """
        llm_start_time = utc_now()

        response_content = ""
        reasoning_content = None
        token_usage = {}

        cancelled_mid_stream = False
        llm_stream = astream_with_retry(messages, model=model)
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

                elif chunk_type == "usage":
                    token_usage = chunk["token_usage"]

                elif chunk_type == "final":
                    if not response_content and chunk.get("content"):
                        response_content = chunk["content"]
                    if not reasoning_content and chunk.get("reasoning_content"):
                        reasoning_content = chunk["reasoning_content"]
                    if not token_usage and chunk.get("token_usage"):
                        token_usage = chunk["token_usage"]

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

        except Exception as llm_error:
            logger.exception(f"LLM call failed: {llm_error}")
            # record-not-emit:错误详情记入 state,turn 末由 decide_terminal 统一发射 ERROR。
            state["error_detail"] = {
                "error": f"LLM call failed: {str(llm_error)}",
                "agent": agent_name,
                "request_id": get_request_id() or None,
            }
            state["completed"] = True
            state["error"] = True
            state["response"] = f"LLM call failed: {str(llm_error)}"
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
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "model": model,
                "duration_ms": llm_duration_ms,
            })
            state["completed"] = True
            state["cancelled"] = True
            # 只有"无工具调用的纯文本"才作为 display 快照写入 state["response"] ——
            # 与 _complete_agent 的不变量一致（有 tool call 的轮次从不把 XML 写进
            # state["response"]）。半截 tool-call XML / 纯 reasoning / TTFT 阶段取消时
            # response_content 不可呈现，留空，由 controller 兜底成占位文案。
            if response_content and "<tool_call>" not in response_content:
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

        await _emit(StreamEventType.LLM_COMPLETE.value, agent_name, {
            "content": response_content,
            "reasoning_content": reasoning_content,
            "token_usage": normalized_usage,
            "model": model,
            "duration_ms": llm_duration_ms,
        })

        accumulate_token_usage(state["execution_metrics"], normalized_usage)

        # Track per-turn token metrics for lead_agent (used by compaction + context budgeting)
        if agent_name == "lead_agent":
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

        return response_content, reasoning_content, normalized_usage

    async def _handle_permission(
        tool_name: str,
        params: dict,
        agent_name: str,
        permission: ToolPermission,
        parser_warnings: Optional[List[str]] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """
        处理权限中断。

        parser_warnings 是本次 tool_call 的 parser 兜底提示。显式 deny 的
        TOOL_COMPLETE 一并带回去，让模型下一轮看到 "你这次的 XML 还有 X 个
        问题、写法应该是 Y"，与其他 TOOL_COMPLETE 路径保持对齐。

        reason 是模型写的调用意图（<reason> 标签），透出到 PERMISSION_REQUEST
        SSE 事件 + interrupt data，让审批弹窗显示 "模型为什么要跑这个工具"。
        缺失（None）时前端按无意图渲染即可。

        Returns:
            True — approved, False — denied（含超时和客户端断开）
        """
        await _emit(StreamEventType.PERMISSION_REQUEST.value, agent_name, {
            "permission_level": permission.value,
            "tool": tool_name,
            "params": params,
            "reason": reason,
        })

        resume_data = await hooks.wait_for_interrupt(message_id, {
            "type": "tool_permission",
            "agent": agent_name,
            "tool_name": tool_name,
            "params": params,
            "reason": reason,
            "permission_level": permission.value,
            "message": f"Tool '{tool_name}' requires {permission.value} permission",
        }, config.PERMISSION_TIMEOUT)

        if resume_data is None:
            logger.warning(f"Permission timeout for tool '{tool_name}' after {config.PERMISSION_TIMEOUT}s, treating as denied")
            await _emit(StreamEventType.PERMISSION_RESULT.value, agent_name, {
                "approved": False, "tool": tool_name, "reason": "timeout",
            })
            # 与显式 deny 路径一样配对发 TOOL_START + TOOL_COMPLETE：否则超时
            # 这次 tool_call 在 event history 里没有 TOOL_COMPLETE，下一轮模型只看到
            # 自己发过 call、却看不到任何结果，可能原样重发。
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "tool": tool_name, "params": params, "reason": reason,
            })
            await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                "tool": tool_name, "success": False,
                "error": (
                    f"Permission request timed out after {config.PERMISSION_TIMEOUT}s "
                    f"with no response, treated as denied. The tool was not executed."
                ),
                "duration_ms": 0,
                "parser_warnings": parser_warnings,
            })
            return False

        is_approved = resume_data.get("approved", False)

        await _emit(StreamEventType.PERMISSION_RESULT.value, agent_name, {
            "approved": is_approved, "tool": tool_name,
        })

        if not is_approved:
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "tool": tool_name, "params": params, "reason": reason,
            })
            await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                "tool": tool_name, "success": False,
                "error": "Permission denied by user. You do not have permission to use this tool.",
                "duration_ms": 0,
                "parser_warnings": parser_warnings,
            })
            return False

        if resume_data.get("always_allow", False):
            allowed = list(state.get("always_allowed_tools", []))
            if tool_name not in allowed:
                allowed.append(tool_name)
            state["always_allowed_tools"] = allowed
            logger.info(f"Tool '{tool_name}' added to always_allowed_tools")

        return True

    async def _maybe_persist_tool_result(
        tool_name: str, tool: BaseTool, result: ToolResult
    ) -> ToolResult:
        """超长成功结果落盘为 artifact，回填预览。
        其他情况（失败 / 工具关闭持久化 / 长度未超限 / manager 缺失 / 落盘异常）
        全部 fail-open 返回原结果，不阻断 tool 调用流程。
        """
        if not result.success:
            return result
        if math.isinf(tool.max_result_size_chars):
            return result
        data = result.data or ""
        if len(data) <= tool.max_result_size_chars:
            return result
        if artifact_service is None or not state.get("session_id"):
            logger.warning(
                f"Cannot persist large tool result for '{tool_name}' "
                f"(size={len(data)}): manager or session unavailable"
            )
            return result

        try:
            aid, version = await artifact_service.persist_tool_result(
                session_id=state["session_id"],
                tool_name=tool_name,
                content=data,
            )
        except Exception as e:
            logger.exception(f"persist_tool_result failed for '{tool_name}': {e}")
            return result  # fail-open

        slice = make_preview_slice(
            artifact_id=aid,
            version=version,
            content_type="text/plain",
            source="tool",
            title=f"Output of {tool_name}",
            full_content=data,
            preview_len=config.TOOL_PERSIST_PREVIEW_LENGTH,
            hint=(
                f"Tool output ({len(data)} chars) saved as artifact '{aid}'. "
                f"Use read_artifact(id='{aid}') for full content; "
                f"preview shows first {config.TOOL_PERSIST_PREVIEW_LENGTH} chars."
            ),
        )
        return ToolResult(
            success=True,
            data=render_artifact_slice(slice),
            metadata={
                **(result.metadata or {}),
                "persisted_artifact_id": aid,
                "original_size_chars": len(data),
            },
        )

    async def _execute_tools(tool_calls: list, agent_name: str) -> None:
        """串行执行工具列表，处理权限中断和 subagent 切换。
        call_subagent 延后到最后执行，确保同一轮的常规工具不会被 break 跳过。
        """
        tool_calls = sorted(tool_calls, key=lambda tc: tc.name == "call_subagent")
        for tool_call in tool_calls:
            if await _check_cancelled():
                break

            # Parser 返回的解析错误 → 直接反馈给 agent
            # 配对发 TOOL_START + TOOL_COMPLETE，与 permission-denied / not-allowed
            # 路径保持一致；让消费者（live SSE / 历史重放）可以无条件假设 START 在
            # COMPLETE 之前，无需 orphan 兜底。
            # Parser 兜底修复登记的提示（截断 / 语法瑕疵等）—— 每个 tool_complete 都带上，
            # 让模型在下一轮看到 "这次解析时我做了什么、你下次应该怎么写"。
            parser_warnings = tool_call.warnings or None

            if tool_call.error:
                await _emit(StreamEventType.TOOL_START.value, agent_name, {
                    "tool": tool_call.name,
                    "params": tool_call.params,
                })
                await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                    "tool": tool_call.name,
                    "success": False,
                    "error": tool_call.error,
                    "duration_ms": 0,
                    "parser_warnings": parser_warnings,
                })
                tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                continue

            tool_name = tool_call.name
            params = tool_call.params
            reason = tool_call.reason  # 模型写的调用意图，透出到审批弹窗（display-only）

            # Agent 工具白名单校验
            if tool_name not in agents[agent_name].tools:
                await _emit(StreamEventType.TOOL_START.value, agent_name, {
                    "tool": tool_name, "params": params,
                })
                await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                    "tool": tool_name, "success": False,
                    "error": f"Tool '{tool_name}' not available for '{agent_name}'",
                    "duration_ms": 0,
                    "parser_warnings": parser_warnings,
                })
                tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                continue

            # 获取工具
            tool = _resolve_tool(tool_name)
            if not tool:
                await _emit(StreamEventType.TOOL_START.value, agent_name, {
                    "tool": tool_name, "params": params,
                })
                await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                    "tool": tool_name, "success": False,
                    "error": f"Tool '{tool_name}' not found",
                    "duration_ms": 0,
                    "parser_warnings": parser_warnings,
                })
                tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                continue

            # call_subagent 特殊处理
            if tool_name == "call_subagent":
                try:
                    result = await tool(**params)
                except Exception as e:
                    logger.exception(f"call_subagent execution error: {e}")
                    result = ToolResult(success=False, error=str(e))

                if result.success:
                    from tools.builtin.call_subagent import CallSubagentTool

                    target_agent = params["agent_name"]
                    instruction = params["instruction"]
                    fresh_start = CallSubagentTool.parse_fresh_start(params)

                    await _emit(StreamEventType.TOOL_START.value, agent_name, {
                        "tool": "call_subagent",
                        "params": {
                            "agent_name": target_agent,
                            "instruction": instruction,
                            "fresh_start": fresh_start,
                        },
                        "reason": reason,
                    })

                    # 注入 instruction 到 subagent 的事件流（仅内存，不推 SSE）
                    # fresh_start=True 时 EventHistory 会把此事件视作 subagent 历史边界，
                    # 之前该 subagent 的 events 对本次调用不可见。
                    state["events"].append(ExecutionEvent(
                        event_type=StreamEventType.SUBAGENT_INSTRUCTION.value,
                        agent_name=target_agent,
                        data={"instruction": instruction, "fresh_start": fresh_start},
                    ))

                    # tool_complete 在 subagent 完成后由 _complete_agent 路径追加。
                    # 此处把 call_subagent 调用本身的 parser_warnings 暂存 state，
                    # _complete_agent 拿到时一并写入 deferred tool_complete。
                    state["pending_subagent_parser_warnings"] = parser_warnings
                    state["current_agent"] = target_agent
                    logger.info(f"Switching to subagent: {target_agent}")
                    tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                    break  # 跳出 tool_calls 循环，继续 while loop
                else:
                    await _emit(StreamEventType.TOOL_START.value, agent_name, {
                        "tool": "call_subagent", "params": params,
                    })
                    await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                        "tool": "call_subagent",
                        "success": False,
                        "error": result.error or "call_subagent failed",
                        "duration_ms": 0,
                        "parser_warnings": parser_warnings,
                    })
                    tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                    continue

            # 权限检查（per-agent 权限覆盖）
            agent_perm_str = agents[agent_name].tools.get(tool_name, tool.permission.value)
            effective_permission = ToolPermission(agent_perm_str)
            if effective_permission == ToolPermission.CONFIRM:
                if tool_name not in state.get("always_allowed_tools", []):
                    approved = await _handle_permission(
                        tool_name, params, agent_name, effective_permission, parser_warnings, reason
                    )
                    if not approved:
                        tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                        continue

            # 执行工具
            tool_start_time = utc_now()
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "tool": tool_name, "params": params, "reason": reason,
            })

            # 可打断 await：cancel flag 在工具在飞期间按 CANCEL_CHECK_INTERVAL 被轮询，
            # 命中即 task.cancel() 在飞工具 —— cancel 延迟不再受 per-tool 超时
            # （bash SANDBOX_COMMAND_TIMEOUT / HttpTool per-MD timeout）支配。
            # 取消落入正常 TOOL_COMPLETE 流（success=False）：START/COMPLETE 配对
            # 不变量保持，下一轮 history 里模型能看到"这次调用被用户打断"。
            # 随后的 _check_cancelled（下个工具前 / while 顶部）置终态 flag 收口。
            try:
                tool_result = await run_cancellable(
                    tool(**params), _is_cancelled, config.CANCEL_CHECK_INTERVAL
                )
            except CooperativeCancelled:
                logger.info(f"Tool '{tool_name}' interrupted by user cancel mid-flight")
                tool_result = ToolResult(
                    success=False,
                    error=(
                        "Cancelled by user while the tool was running. "
                        "Side effects may or may not have been applied "
                        "(the operation was already in flight)."
                    ),
                )
            except Exception as e:
                logger.exception(f"Tool '{tool_name}' execution error: {e}")
                tool_result = ToolResult(success=False, error=str(e))

            tool_end_time = utc_now()
            tool_duration_ms = int((tool_end_time - tool_start_time).total_seconds() * 1000)

            # 超长成功结果统一落盘为 artifact，回填预览（fail-open）
            tool_result = await _maybe_persist_tool_result(tool_name, tool, tool_result)

            # 识图:把图块 data-URI 从将入事件的 metadata 里摘出 → 存进本 turn 的
            # state["vision_blocks"](仅内存、不持久化、跨轮自然失效);事件只留引用
            # (artifact_id/version/content_type)。context build 据 state 还原:本轮命中
            # → 注入图块;下一轮 state 已空 → 占位文本(模型再 read_artifact 即可重看)。
            # 字节绝不进事件表(撑爆 + 与「blob 有专属持久家」冲突)。
            tc_metadata = tool_result.metadata or None
            _img = tc_metadata.get("image") if tc_metadata else None
            if isinstance(_img, dict) and "data_uri" in _img:
                state.setdefault("vision_blocks", {})[
                    (_img.get("artifact_id"), _img.get("version"))
                ] = _img["data_uri"]
                tc_metadata = {
                    **tc_metadata,
                    "image": {k: v for k, v in _img.items() if k != "data_uri"},
                }

            await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                "tool": tool_name,
                "success": tool_result.success,
                "result_data": tool_result.data if tool_result.success else None,
                "error": tool_result.error if not tool_result.success else None,
                "duration_ms": tool_duration_ms,
                "params": params,
                "metadata": tc_metadata,
                "parser_warnings": parser_warnings,
            })


            tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1

    async def _check_cancelled() -> bool:
        # 同走软化谓词:探针异常在 loop 顶/工具间穿出会被 while 外层
        # except Exception 记成 turn ERROR(一次 Redis 抖动杀掉整个 turn)。
        if await _is_cancelled():
            state["completed"] = True
            state["cancelled"] = True
            state["response"] = state.get("response", "") or ""
            return True
        return False

    # ── main loop ──
    # (_emit already bound to artifact_service above, before upload staging;
    #  unbound in the finally below.)

    try:
        while not state["completed"]:
            if await _check_cancelled():
                break

            current_agent_name = state["current_agent"]
            if current_agent_name not in agents:
                logger.error(f"Agent '{current_agent_name}' not found")
                state["error"] = True
                state["response"] = f"Agent '{current_agent_name}' not found"
                # record-not-emit:turn 末由 decide_terminal 统一发射 ERROR。
                state["error_detail"] = {
                    "error": f"Agent '{current_agent_name}' not found",
                    "agent": current_agent_name,
                    "request_id": get_request_id() or None,
                }
                break

            messages, reminder = await _build_context(current_agent_name)

            # agent_start 持久化「发给模型的非历史输入」：静态 system_prompt + 动态 reminder。
            # 历史可由 event 流确定性重放，这两块（尤其 reminder：现拼即丢、不入 event）补上后，
            # admin 即可零重生成、忠实重建这一发的完整 prompt。reminder 不进 LLM 输入缓存前缀，
            # 落进事件 payload 对 prompt cache 零影响。
            await _emit(StreamEventType.AGENT_START.value, current_agent_name, {
                "agent": current_agent_name,
                "system_prompt": messages[0]["content"] if messages and messages[0].get("role") == "system" else None,
                "reminder": reminder,
            })

            # 守卫:format_messages_for_debug 会遍历 messages,识图块列表里若有图(已压成
            # 摘要、不吐 base64,但仍要遍历)——非 DEBUG 时跳过 eager 求值。
            if logger.debug_mode:
                logger.debug(f"[{current_agent_name}] Messages:\n{format_messages_for_debug(messages)}")

            # 调用 LLM（流式）
            llm_result = await _call_llm(messages, current_agent_name, agents[current_agent_name].model)
            if llm_result is None:
                break

            response_content, reasoning_content, normalized_usage = llm_result

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
                    agent_name=current_agent_name,
                    input_tokens=normalized_usage["input_tokens"],
                    output_tokens=normalized_usage["output_tokens"],
                )
            except CooperativeCancelled:
                # 用户 cancel 落在 compaction LLM 调用期间（原本是最长的盲窗：
                # COMPACTION_TIMEOUT 秒）。maybe_trigger 的 except Exception 已配对
                # 追加 success=False 的 compaction_summary（EventHistory 跳过，无
                # boundary）—— 此处只需路由到 CANCELLED 终态，不能落进下面的
                # ERROR 分支。
                logger.info(
                    f"Compaction for {current_agent_name} interrupted by user cancel"
                )
                state["completed"] = True
                state["cancelled"] = True
                state["response"] = state.get("response", "") or ""
                break
            except Exception as compact_error:
                logger.error(f"Compaction failed for {current_agent_name}: {compact_error}")
                # record-not-emit:turn 末由 decide_terminal 统一发射 ERROR。
                state["error_detail"] = {
                    "error": f"Compaction failed: {str(compact_error)}",
                    "agent": current_agent_name,
                    "request_id": get_request_id() or None,
                }
                state["completed"] = True
                state["error"] = True
                state["response"] = f"Compaction failed: {str(compact_error)}"
                break

            # 解析工具调用
            tool_calls = parse_tool_calls(response_content)

            if not tool_calls:
                # Lead 无工具调用但队列中有待处理消息 → 不退出，继续循环
                # 这处理了 inject 消息在最后一次 LLM 调用期间到达的情况
                if current_agent_name == "lead_agent":
                    pending = await hooks.drain_messages(message_id)
                    if pending:
                        for msg in pending:
                            wrapped = (
                                "[The user has injected a message during execution. "
                                "Consider this input and adjust your approach as needed.]\n"
                                + msg
                            )
                            await _emit(StreamEventType.QUEUED_MESSAGE.value, "lead_agent", {"content": wrapped})
                        continue  # 回到 while loop 顶部，下次 _build_context 会看到新事件

                # 无待处理消息 → 正常完成当前 agent
                await _complete_agent(current_agent_name, response_content)
                tool_round_count.pop(current_agent_name, None)
                continue

            # 串行执行工具
            await _execute_tools(tool_calls, current_agent_name)

    except Exception as e:
        logger.exception(f"Execution loop error: {e}")
        # record-not-emit:turn 末由 decide_terminal 统一发射 ERROR。
        state["error_detail"] = {
            "error": str(e),
            "agent": state.get("current_agent"),
            "request_id": get_request_id() or None,
        }
        state["error"] = True
        state["response"] = f"Execution failed: {str(e)}"

    finally:
        if _bind_emit:
            _bind_emit(None)

    # 完成 metrics
    finalize_metrics(state["execution_metrics"])

    return state

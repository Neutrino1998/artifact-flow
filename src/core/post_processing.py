"""
Post-processing ledger — 把 ConversationTurnHandler 后半段的"决策 + IO 进度"
显式化成一个 dataclass + 几个纯函数,让 cancel handler 不需要重新推断 "我现在在哪、
该补什么"。

问题：
    post-processing 是一段"先决策再多次写库"的串行流程,每个 await 之后 cancel 都可
    能落下。success path 算一次 (terminal, response),late-cancel handler 走另一段
    if/elif 再算一次 —— 一旦两边漏对一种 case(比如 engine 已 COMPLETE 而 late-cancel
    误写 system placeholder),events 表跟 Message.response 显示就矛盾。本质上这是一
    个状态机,只是状态散在局部变量里,每补一个洞会再冒一个 phase edge case。

设计：
    - PostProcessState        所有跨 await 状态(布尔进度 + 已决定的 terminal/response)
    - decide_terminal()       纯决策(无 IO):runtime stop reason + final_state → terminal
    - ensure_terminal()       late-cancel handler 用:已有本轮 terminal 就 adopt,否则按事实终因生成
    - choose_response_for_terminal()
                              terminal_type × stop_reason → display 字符串。SUCCESS PATH
                              和 late-cancel handler 都调它 —— 单一真相源,杜绝漂移

不变量(由结构而非纪律保证)：
    1. events 落库前不写 Message.response          (caller 检查 pp.events_persisted)
    2. response slot 一旦 claimed 不再覆盖         (caller 检查 pp.response_update_attempted)
    3. 已有 semantic terminal 不被 late-cancel 改  (ensure_terminal adopt)
    4. 只有 runtime external cancel 才写 system placeholder
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from config import config
from core.agent_runtime import StopReason
from core.events import ExecutionEvent, StreamEventType, TERMINAL_EVENT_TYPES
from utils.instance import INSTANCE_ID


@dataclass
class PostProcessState:
    """跨 await 的 post-processing ledger。

    success path 顺序填充字段;late-cancel handler 读字段决定补救动作。
    每个布尔字段对应一段 IO 的 "已成功完成";Optional 字段对应"已决策"。
    """

    conversation_id: str
    message_id: str
    final_state: Dict[str, Any]
    # AgentRuntime 已经裁定的事实终因。必须进入 ledger，不能在 late-cancel 时
    # 从 state flags / terminal 是否已 append 反推；否则 COMPLETE 会被误猜成 CANCELLED。
    stop_reason: StopReason

    # 决策(decide_terminal / ensure_terminal 设置)
    terminal_event: Optional[ExecutionEvent] = None  # None = 尚未决策;decide_terminal 后必非 None
    terminal_type: Optional[str] = None              # COMPLETE / ERROR / CANCELLED
    flush_error: Optional[str] = None                # artifact flush 异常文本,被 decide_terminal 转成 ERROR terminal

    # IO 进度
    conv_alive: Optional[bool] = None
    artifact_flush_completed: bool = False          # success 或已记录 flush_error；cancel-mid-await 保持 False
    terminal_appended: bool = False                  # terminal_event 已加入 final_state["events"]
    events_persisted: bool = False                   # _persist_events 返回 True
    # response_update_attempted 必须在 `await update_response_async` 之前 set,
    # 不是之后 —— cancel 可能落在 await 中间(DB commit 已发出但 Python 没看到返回),
    # late handler 看 attempted=False 会再写一遍 placeholder 覆盖真实 response。
    # 见 ConversationTurnHandler.run 的 race rationale 注释。
    response_update_attempted: bool = False


# ============================================================================
# 决策函数(纯,无 IO)
# ============================================================================


def decide_terminal(pp: PostProcessState) -> None:
    """根据 runtime 事实终因决定 terminal_event + terminal_type。

    调用时机:exists/flush 之后、persist 之前。一次决策,后续 success path 和
    cancel handler 都读 pp。

    特殊语义:
    - ERROR 时 engine/runtime 不 append ERROR,只把详情记进
      state["error_detail"]。decide_terminal 在 flush 之后据此构建唯一的
      ERROR terminal_event,由 ConversationTurnHandler 统一 append + yield
      —— 好处是 ERROR 也走 flush 后路径,无 decide_terminal 之外的第二个 ERROR 发射点。
    - flush_error 优先于所有 runtime stop reasons：artifact 持久化失败是 Handler
      自己产生的 ERROR,同样构建新的 terminal_event。
    """
    s = pp.final_state
    metrics = s.get("execution_metrics", {})
    response = s.get("response", "")

    if pp.flush_error:
        pp.terminal_type = StreamEventType.ERROR.value
        pp.terminal_event = ExecutionEvent(
            event_type=StreamEventType.ERROR.value,
            agent_name=None,
            data={
                "success": False,
                "conversation_id": pp.conversation_id,
                "message_id": pp.message_id,
                "error": pp.flush_error,
                "instance_id": INSTANCE_ID,
                "execution_metrics": metrics,
            },
        )
        return

    if pp.stop_reason == StopReason.TIMEOUT:
        pp.terminal_type = StreamEventType.TIMED_OUT.value
        pp.terminal_event = ExecutionEvent(
            event_type=StreamEventType.TIMED_OUT.value,
            agent_name=None,
            data={
                "success": False,
                "timed_out": True,
                "conversation_id": pp.conversation_id,
                "message_id": pp.message_id,
                # SSE data 带 response 是历史约定(前端用作 snapshot,与 CANCELLED 同构)
                "response": config.TIMED_OUT_RESPONSE,
                "execution_metrics": metrics,
            },
        )
        return

    if pp.stop_reason in {
        StopReason.COOPERATIVE_CANCEL,
        StopReason.EXTERNAL_CANCEL,
    }:
        pp.terminal_type = StreamEventType.CANCELLED.value
        is_external = pp.stop_reason == StopReason.EXTERNAL_CANCEL
        # SSE 数据里带 response 是历史约定(前端用作 snapshot)
        display = (
            config.CANCELLED_RESPONSE_BY_SYSTEM
            if is_external
            else response or config.CANCELLED_RESPONSE_BY_USER
        )
        data = {
            "success": False,
            "cancelled": True,
            "conversation_id": pp.conversation_id,
            "message_id": pp.message_id,
            "response": display,
            "execution_metrics": metrics,
        }
        if is_external:
            data["reason"] = "external_cancel"
        pp.terminal_event = ExecutionEvent(
            event_type=StreamEventType.CANCELLED.value,
            agent_name=None,
            data=data,
        )
        return

    if pp.stop_reason == StopReason.ERROR:
        # 统一终态发射点:engine/runtime 的内部错误不 emit ERROR,只把详情记进
        # state["error_detail"];这里(flush 之后)构建并发射唯一的 ERROR 终态,带
        # request_id。Handler 的 append + yield 自动接手 —— engine-error 也走
        # flush 后路径,不再有 decide_terminal 之外的第二个 ERROR 发射点。
        detail = s.get("error_detail") or {}
        pp.terminal_type = StreamEventType.ERROR.value
        pp.terminal_event = ExecutionEvent(
            event_type=StreamEventType.ERROR.value,
            agent_name=detail.get("agent"),
            data={
                "success": False,
                "conversation_id": pp.conversation_id,
                "message_id": pp.message_id,
                "error": detail.get("error") or response or "An error occurred during execution.",
                "agent": detail.get("agent"),
                "request_id": detail.get("request_id"),
                # 受理实例,创建时冻结(decide_terminal 与故障引擎同进程,常量即正确)
                "instance_id": INSTANCE_ID,
                "execution_metrics": metrics,
            },
        )
        return

    # StopReason 是本地封闭枚举；前面的分支已穷尽其余终因，剩余即 COMPLETE。
    pp.terminal_type = StreamEventType.COMPLETE.value
    pp.terminal_event = ExecutionEvent(
        event_type=StreamEventType.COMPLETE.value,
        agent_name=None,
        data={
            "success": True,
            "conversation_id": pp.conversation_id,
            "message_id": pp.message_id,
            "response": response,
            "execution_metrics": metrics,
        },
    )


def ensure_terminal(pp: PostProcessState) -> None:
    """late-cancel handler 调用:保证 final_state["events"] 末尾有一个 terminal。

    分三种情况:
    1. pp.terminal_appended 已是 True:啥都不做(success path 已经 append 过,或
       decide_terminal 标记过 ERROR 路径"engine 自己 append 了")。
    2. final_state["events"] 里有本轮 terminal 但 pp 没标(cancel 卡在
       decide_terminal 和 persist 之间):adopt 它 —— 把 type 抄进 pp,标
       terminal_appended,不重复 append。这种情况下 engine 在语义上已经完成,cancel
       只命中了基础设施,要保留 engine 的终态语义。
    3. final_state["events"] 里没有本轮 terminal:按 pp.stop_reason 调统一 dispatcher
       生成并 append。不能从“尚无 terminal”推断 external cancel。
    """
    if pp.terminal_appended:
        return

    terminal_types = TERMINAL_EVENT_TYPES  # 权威集合(core.events),含 TIMED_OUT
    # 只看本轮(非 historical)的 events —— state["events"] 是 [historical from
    # parent turns, current turn 实时 append] 的拼接,_persist_events 只写非
    # historical 段。如果误 adopt parent 轮的 historical terminal,本轮就缺终态:
    # 合成路径被跳过 → persist 过滤掉 historical → DB 里本轮只有 LLM_COMPLETE 之类,
    # 没有 COMPLETE/ERROR/CANCELLED 收尾。下一轮 EventHistory 重建会撞到"无终态"
    # 的半截 turn。
    # 从后往前扫:同 turn 里同时间只可能有一个 terminal,reverse 是 defense-in-depth
    # —— 真有多个时 adopt 最新那个语义最对。
    existing = next(
        (
            e for e in reversed(pp.final_state.get("events", []))
            if e.event_type in terminal_types
            and not getattr(e, "is_historical", False)
        ),
        None,
    )
    if existing is not None:
        pp.terminal_appended = True
        pp.terminal_event = existing
        if pp.terminal_type is None:
            pp.terminal_type = existing.event_type
        return

    decide_terminal(pp)
    if pp.terminal_event is not None:
        pp.final_state["events"].append(pp.terminal_event)
        pp.terminal_appended = True


def choose_response_for_terminal(pp: PostProcessState) -> str:
    """给定 pp 已决定的 terminal,返回 Message.response 应写入的字符串。

    单一真相源:success path 和 late-cancel handler 都调它。任何路径想往
    Message.response 写,都必须经过这个函数 —— 防止 "engine 已 COMPLETE 但 cancel
    handler 误写 system placeholder" 这类漂移。

    映射:
    - COMPLETE           → state["response"](engine 的真实输出)
    - TIMED_OUT          → TIMED_OUT_RESPONSE(基础设施事件,忽略 state.response)
    - ERROR              → state["response"] 或 "An error occurred during execution."
    - CANCELLED + cooperative stop → state["response"] 或 CANCELLED_RESPONSE_BY_USER
    - CANCELLED + external stop    → CANCELLED_RESPONSE_BY_SYSTEM

    Caller 责任:调用前必须确认 pp.events_persisted=True 且 pp.response_update_attempted=False,
    否则违反"events-first"和"slot-claim"不变量。这里不做检查 —— 让 caller 显式表达意图。
    """
    response = pp.final_state.get("response", "")

    if pp.terminal_type == StreamEventType.COMPLETE.value:
        return response

    if pp.terminal_type == StreamEventType.TIMED_OUT.value:
        # 超时是基础设施事件,跟 engine 是否产出无关(与 external cancel 同理):
        # 始终写 TIMED_OUT_RESPONSE 标记"超时中止",忽略 state.response。
        return config.TIMED_OUT_RESPONSE

    if pp.terminal_type == StreamEventType.ERROR.value:
        return response or "An error occurred during execution."

    if pp.terminal_type == StreamEventType.CANCELLED.value:
        if pp.stop_reason == StopReason.EXTERNAL_CANCEL:
            return config.CANCELLED_RESPONSE_BY_SYSTEM
        return response or config.CANCELLED_RESPONSE_BY_USER

    # 无 terminal —— 不该被调用到,fail-safe 返回空串
    return ""

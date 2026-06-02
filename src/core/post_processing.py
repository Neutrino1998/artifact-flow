"""
Post-processing ledger — 把分散在 controller.stream_execute 后半段的"决策 + IO 进度"
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
    - decide_terminal()       纯决策(无 IO):final_state → (terminal_event, terminal_type, response_text)
    - ensure_terminal()       late-cancel handler 用:已有 terminal 就 adopt,没有就 synthesize external CANCELLED
    - choose_response_for_terminal()
                              terminal_type × cancel_source → display 字符串。SUCCESS PATH
                              和 late-cancel handler 都调它 —— 单一真相源,杜绝漂移

不变量(由结构而非纪律保证)：
    1. events 落库前不写 Message.response          (caller 检查 pp.events_persisted)
    2. response slot 一旦 claimed 不再覆盖         (caller 检查 pp.response_update_attempted)
    3. 已有 semantic terminal 不被 late-cancel 改  (ensure_terminal adopt)
    4. 只在无 terminal 时才写 system placeholder   (choose_response_for_terminal 看 cancel_source)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from config import config
from core.events import ExecutionEvent, StreamEventType, TERMINAL_EVENT_TYPES


@dataclass
class PostProcessState:
    """跨 await 的 post-processing ledger。

    success path 顺序填充字段;late-cancel handler 读字段决定补救动作。
    每个布尔字段对应一段 IO 的 "已成功完成";Optional 字段对应"已决策"。
    """

    conversation_id: str
    message_id: str
    final_state: Dict[str, Any]

    # 决策(decide_terminal / ensure_terminal 设置)
    terminal_event: Optional[ExecutionEvent] = None  # None = "engine 已自己 append 了,别再 append"
    terminal_type: Optional[str] = None              # COMPLETE / ERROR / CANCELLED
    cancel_source: Optional[str] = None              # "cooperative" / "external" (仅 terminal_type=CANCELLED 时有效)
    flush_error: Optional[str] = None                # artifact flush 异常文本,被 decide_terminal 转成 ERROR terminal

    # IO 进度
    conv_alive: Optional[bool] = None
    artifacts_flushed: bool = False
    terminal_appended: bool = False                  # terminal_event 已加入 final_state["events"]
    events_persisted: bool = False                   # _persist_events 返回 True
    # response_update_attempted 必须在 `await update_response_async` 之前 set,
    # 不是之后 —— cancel 可能落在 await 中间(DB commit 已发出但 Python 没看到返回),
    # late handler 看 attempted=False 会再写一遍 placeholder 覆盖真实 response。
    # 见 controller.stream_execute 的 race rationale 注释。
    response_update_attempted: bool = False
    response_updated: bool = False
    metadata_updated: bool = False

    # SSE 用:success path 通过 controller yield 出去的终态事件 dict(派生自 terminal_event)
    sse_terminal: Optional[Dict[str, Any]] = field(default=None, repr=False)


# ============================================================================
# 决策函数(纯,无 IO)
# ============================================================================


def uploads_persisted(pp: PostProcessState) -> bool:
    """前端"要不要清掉输入框附件"的唯一真相:用户这轮的上传是否真的落库。

    不是字面"flush 动作成没成":
      pp.artifacts_flushed = flush_all 调用成功(对空集也 True);
      uploads_rolled_back   = staging 失败时引擎回滚了上传(WS 摘除,flush 空转
                              成功却没东西落)。
    二者相减才是真相 —— 其余路径 uploads_rolled_back=False,退化回 pp.artifacts_flushed。

    单一真相源:decide_terminal 的四个终态、以及 controller 两个 transport 层直发
    ERROR(events 持久化失败 / post-processing 异常)都经此函数,确保 artifacts_flushed
    bit 在所有终态一致 —— 否则缺 bit 的 ERROR 会被前端当"未落库"处理而重复 staging。
    """
    return pp.artifacts_flushed and not pp.final_state.get("uploads_rolled_back", False)


def decide_terminal(pp: PostProcessState) -> None:
    """根据 final_state 决定 terminal_event + terminal_type + cancel_source。

    调用时机:exists/flush 之后、persist 之前。一次决策,后续 success path 和
    cancel handler 都读 pp。

    特殊语义:
    - has_error 时 engine 已经把 ERROR 事件 append 到 final_state["events"](见
      run_engine 的 except Exception 分支)。decide_terminal 设 terminal_type=ERROR
      + terminal_appended=True,但留 terminal_event=None 防止 controller 二次 append。
    - flush_error 优先于 has_error / is_cancelled:artifact 持久化失败是 controller
      自己产生的 ERROR,要 append 新事件。
    """
    s = pp.final_state
    has_error = s.get("error", False)
    is_cancelled = s.get("cancelled", False)
    timed_out = s.get("timed_out", False)
    metrics = s.get("execution_metrics", {})
    response = s.get("response", "")

    # 前端"要不要清掉输入框附件"的依据(终态 data 里叫 artifacts_flushed)。
    # 见 uploads_persisted() —— controller 的 transport 层直发 ERROR 也复用同一函数。
    persisted = uploads_persisted(pp)

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
                "execution_metrics": metrics,
                # flush 失败 → uploads_persisted=False → 前端保留输入框附件供重试
                "artifacts_flushed": persisted,
            },
        )
        return

    # timed_out 与 is_cancelled 是兄弟终因(都"非错误地中止执行"),互斥:超时路径
    # (run_engine 的 except TimeoutError)只置 timed_out,协作式取消只置 cancelled。
    # 放在 flush_error 之后保持"持久化失败即便在超时轮也以 ERROR 暴露"的既有
    # 优先级(flush_error > 终因 > has_error > complete)。
    if timed_out:
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
                "artifacts_flushed": persisted,
            },
        )
        return

    if is_cancelled:
        pp.terminal_type = StreamEventType.CANCELLED.value
        pp.cancel_source = "cooperative"
        # SSE 数据里带 response 是历史约定(前端用作 snapshot)
        display = response or config.CANCELLED_RESPONSE_BY_USER
        pp.terminal_event = ExecutionEvent(
            event_type=StreamEventType.CANCELLED.value,
            agent_name=None,
            data={
                "success": False,
                "cancelled": True,
                "conversation_id": pp.conversation_id,
                "message_id": pp.message_id,
                "response": display,
                "execution_metrics": metrics,
                "artifacts_flushed": persisted,
            },
        )
        return

    if has_error:
        # 统一终态发射点:engine/controller 的内部错误不再自己 emit ERROR,只把详情记进
        # state["error_detail"];这里(flush 之后)构建并发射唯一的 ERROR 终态,带
        # request_id + artifacts_flushed。controller 现有的 append + yield 自动接手。
        # 好处:engine-error 也走 flush 后路径 → artifacts_flushed 必带且正确,前端不再
        # 靠"缺字段"猜测。(transport 层错误 —— stream not-found / forwarder 异常 —— 仍
        # 在 decide_terminal 之外、无此 bit,前端对缺字段 error 默认保留附件兜底。)
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
                "execution_metrics": metrics,
                "artifacts_flushed": persisted,
            },
        )
        return

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
            "artifacts_flushed": persisted,
        },
    )


def ensure_terminal(pp: PostProcessState) -> None:
    """late-cancel handler 调用:保证 final_state["events"] 末尾有一个 terminal。

    分三种情况:
    1. pp.terminal_appended 已是 True:啥都不做(success path 已经 append 过,或
       decide_terminal 标记过 ERROR 路径"engine 自己 append 了")。
    2. final_state["events"] 里有 terminal 但 pp 没标(cancel 卡在 decide_terminal
       和 persist 之间或更早):adopt 它 —— 把 type/cancel_source 抄进 pp,标
       terminal_appended,不重复 append。这种情况下 engine 在语义上已经完成,cancel
       只命中了基础设施,要保留 engine 的终态语义。
    3. final_state["events"] 里也没有 terminal:cancel 真的中断了执行,合成一个
       external CANCELLED 并 append。
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
        if pp.terminal_type is None:
            pp.terminal_type = existing.event_type
            if existing.event_type == StreamEventType.CANCELLED.value:
                # engine_task 路径 append 的 CANCELLED 带 reason="external_cancel";
                # cooperative path 走 decide_terminal 不带 reason 字段(只在 data 里有
                # response/cancelled/...)。用 reason 字段存在与否区分来源 —— engine_task
                # 路径不走 decide_terminal,所以这里推断必要。
                data = existing.data if isinstance(existing.data, dict) else {}
                pp.cancel_source = "external" if data.get("reason") else "cooperative"
        return

    # 没有现成 terminal 可 adopt。engine 是否已记录了真实终因?
    # 统一后 engine 内部错误只记 state["error"]+error_detail、不再实时 append ERROR,
    # 所以 late-cancel 落在 decide_terminal 之前时,events 里没有 ERROR 可 adopt。此时若
    # engine 语义上已到达 error/timeout/cooperative-cancel(只是还没被 decide_terminal
    # 发射),应保留该真实终因 —— 委托 decide_terminal 统一构建,而不是一律掩成 external
    # CANCELLED 把错误/超时丢掉(那是"事实上出错的轮被记成取消"的静默失真)。
    s = pp.final_state
    if s.get("error") or s.get("timed_out") or s.get("cancelled"):
        decide_terminal(pp)
        if pp.terminal_event is not None and not pp.terminal_appended:
            pp.final_state["events"].append(pp.terminal_event)
            pp.terminal_appended = True
        return

    # engine 啥终因都没记 → cancel 真的中断了执行中途 → 合成 external CANCELLED
    pp.final_state["cancelled"] = True  # 同步 state,future code path 看得到
    pp.terminal_type = StreamEventType.CANCELLED.value
    pp.cancel_source = "external"
    pp.terminal_event = ExecutionEvent(
        event_type=StreamEventType.CANCELLED.value,
        agent_name=None,
        data={
            "success": False,
            "cancelled": True,
            "conversation_id": pp.conversation_id,
            "message_id": pp.message_id,
            "reason": "external_cancel_post_processing",
            # late-cancel 可能落在 flush 之前或之后,读 ledger 的真值
            "artifacts_flushed": pp.artifacts_flushed,
        },
    )
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
    - CANCELLED (coop)   → state["response"] 或 CANCELLED_RESPONSE_BY_USER
    - CANCELLED (ext)    → CANCELLED_RESPONSE_BY_SYSTEM

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
        if pp.cancel_source == "external":
            return config.CANCELLED_RESPONSE_BY_SYSTEM
        # cooperative(用户主动)或未指定(防御性 fallback,但 decide_terminal /
        # ensure_terminal 都会显式 set)
        return response or config.CANCELLED_RESPONSE_BY_USER

    # 无 terminal —— 不该被调用到,fail-safe 返回空串
    return ""


def make_external_cancelled_event(
    conversation_id: str,
    message_id: str,
    reason: str,
    execution_metrics: Optional[Dict[str, Any]] = None,
) -> ExecutionEvent:
    """engine_task 的 except CancelledError 用:append CANCELLED terminal with reason。

    抽出来主要是为了让"external CANCELLED 长什么样"只在一个地方定义 —— ensure_terminal
    合成的 CANCELLED 和 engine_task 直接 append 的 CANCELLED 用同一个 builder。
    """
    data: Dict[str, Any] = {
        "success": False,
        "cancelled": True,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "reason": reason,
        # 外部取消发生在 execute_loop 内,post-processing 的 flush_all 从未运行 →
        # 上传未落库,前端据此保留输入框附件(虽此终态通常因 consumer 已断而不下发)
        "artifacts_flushed": False,
    }
    if execution_metrics is not None:
        data["execution_metrics"] = execution_metrics
    return ExecutionEvent(
        event_type=StreamEventType.CANCELLED.value,
        agent_name=None,
        data=data,
    )

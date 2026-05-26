"""
post_processing.py 纯函数单元测试 — 参数化矩阵锁定 4 条 invariant：

1. events 落库前不写 Message.response          (caller 检查 pp.events_persisted)
2. response slot 一旦 claimed 不再覆盖          (caller 检查 pp.response_update_attempted)
3. 已有 semantic terminal 不被 late-cancel 改   (ensure_terminal adopt 既有 terminal)
4. 只在无 terminal 时才写 system placeholder    (choose_response_for_terminal 看 cancel_source)

invariant 1/2 由 controller 调用方负责检查,不在这里测;矩阵测试覆盖 3/4 的核心决策
函数 (decide_terminal / ensure_terminal / choose_response_for_terminal)。controller
集成行为见 tests/core/test_controller_cancel_persist.py。
"""

import pytest

from config import config
from core.events import ExecutionEvent, StreamEventType
from core.post_processing import (
    PostProcessState,
    choose_response_for_terminal,
    decide_terminal,
    ensure_terminal,
    make_external_cancelled_event,
)


# ============================================================================
# choose_response_for_terminal — invariant 4: (terminal_type × cancel_source) → display
# ============================================================================


class TestChooseResponseMatrix:
    """穷举 (terminal_type, cancel_source, state.response) → 期望 display 字符串。

    success path 跟 late-cancel handler 都调这个函数 —— 一旦矩阵改变,两处行为同步
    变化,杜绝"两份计算各自漂"。
    """

    @pytest.mark.parametrize("state_response,expected", [
        ("real engine output", "real engine output"),
        ("", ""),  # COMPLETE + 空 response → 留空(由 caller 判断要不要写;前端不渲染)
    ])
    def test_complete_returns_real_response(self, state_response, expected):
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": state_response},
            terminal_type=StreamEventType.COMPLETE.value,
        )
        assert choose_response_for_terminal(pp) == expected

    @pytest.mark.parametrize("state_response,expected", [
        ("Engine error: NPE", "Engine error: NPE"),
        ("", "An error occurred during execution."),
    ])
    def test_error_returns_response_or_fallback(self, state_response, expected):
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": state_response},
            terminal_type=StreamEventType.ERROR.value,
        )
        assert choose_response_for_terminal(pp) == expected

    @pytest.mark.parametrize("state_response,expected", [
        ("user said this before cancel", "user said this before cancel"),
        ("", config.CANCELLED_RESPONSE_BY_USER),
    ])
    def test_cooperative_cancel_returns_user_placeholder(self, state_response, expected):
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": state_response},
            terminal_type=StreamEventType.CANCELLED.value,
            cancel_source="cooperative",
        )
        assert choose_response_for_terminal(pp) == expected

    @pytest.mark.parametrize("state_response", [
        "",
        "engine had real content but external cancel hit",  # state.response is IGNORED for external
    ])
    def test_external_cancel_always_returns_system_placeholder(self, state_response):
        """external cancel 是基础设施事件,跟 engine 是否产出无关 —— 始终写
        BY_SYSTEM 标记"非用户主观取消"。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": state_response},
            terminal_type=StreamEventType.CANCELLED.value,
            cancel_source="external",
        )
        assert choose_response_for_terminal(pp) == config.CANCELLED_RESPONSE_BY_SYSTEM

    @pytest.mark.parametrize("cancel_source", [None, "external", "cooperative"])
    @pytest.mark.parametrize("state_response", ["", "engine had partial output before timeout"])
    def test_timed_out_always_returns_timeout_placeholder(self, cancel_source, state_response):
        """超时是基础设施事件:始终写 TIMED_OUT_RESPONSE,与 cancel_source / state.response 无关。
        (adopt 路径下 cancel_source 可能为 None;无论如何都走同一占位串。)"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": state_response},
            terminal_type=StreamEventType.TIMED_OUT.value,
            cancel_source=cancel_source,
        )
        assert choose_response_for_terminal(pp) == config.TIMED_OUT_RESPONSE

    def test_no_terminal_returns_empty(self):
        """无 terminal_type 时 fail-safe 返回空串(理论上不该被 caller 走到)。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": "anything"},
        )
        assert choose_response_for_terminal(pp) == ""


# ============================================================================
# decide_terminal — 优先级 + 终态种类
# ============================================================================


class TestDecideTerminal:

    def test_complete_path(self):
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": "hello", "execution_metrics": {"ms": 100}},
        )
        decide_terminal(pp)
        assert pp.terminal_type == StreamEventType.COMPLETE.value
        assert pp.terminal_event is not None
        assert pp.terminal_event.data["response"] == "hello"
        assert pp.terminal_event.data["success"] is True
        assert pp.cancel_source is None

    def test_cooperative_cancel_path(self):
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": "", "cancelled": True},
        )
        decide_terminal(pp)
        assert pp.terminal_type == StreamEventType.CANCELLED.value
        assert pp.cancel_source == "cooperative"
        # SSE data carries display fallback for snapshot
        assert pp.terminal_event.data["response"] == config.CANCELLED_RESPONSE_BY_USER

    def test_error_path_does_not_double_append(self):
        """engine 已 append ERROR,decide_terminal 设 type 但留 event=None 防二次 append。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": "Engine error: x", "error": True},
        )
        decide_terminal(pp)
        assert pp.terminal_type == StreamEventType.ERROR.value
        assert pp.terminal_event is None
        assert pp.terminal_appended is True

    def test_flush_error_overrides_cancelled(self):
        """flush_error 在 controller 里产生,作为新的 ERROR terminal append。
        是 controller 自身写的失败,优先级高于 engine 报告的 cancelled。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": "", "cancelled": True},
            flush_error="Artifact persistence failed: disk full",
        )
        decide_terminal(pp)
        assert pp.terminal_type == StreamEventType.ERROR.value
        assert pp.terminal_event is not None
        assert "disk full" in pp.terminal_event.data["error"]

    def test_timed_out_path(self):
        """timed_out → 一等 TIMED_OUT 终态,success=False,data 带 TIMED_OUT_RESPONSE。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": "partial", "timed_out": True,
                         "execution_metrics": {"ms": 1800000}},
        )
        decide_terminal(pp)
        assert pp.terminal_type == StreamEventType.TIMED_OUT.value
        assert pp.terminal_event is not None
        assert pp.terminal_event.data["success"] is False
        assert pp.terminal_event.data["timed_out"] is True
        assert pp.terminal_event.data["response"] == config.TIMED_OUT_RESPONSE
        assert pp.cancel_source is None

    def test_flush_error_overrides_timed_out(self):
        """优先级 flush_error > timed_out:超时轮里 artifact 持久化失败仍以 ERROR 暴露,
        与 flush_error > cancelled 的既有优先级一致。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"response": "", "timed_out": True},
            flush_error="Artifact persistence failed: disk full",
        )
        decide_terminal(pp)
        assert pp.terminal_type == StreamEventType.ERROR.value
        assert "disk full" in pp.terminal_event.data["error"]


# ============================================================================
# ensure_terminal — invariant 3: 已有 semantic terminal 被保留
# ============================================================================


class TestEnsureTerminal:

    def test_idempotent_when_already_appended(self):
        """terminal_appended=True → noop(success path 已 append 过)。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": []},
            terminal_appended=True,
        )
        ensure_terminal(pp)
        assert pp.final_state["events"] == []

    def test_adopts_existing_complete_terminal(self):
        """events 已有 COMPLETE,但 pp 没标 → adopt,不合成 CANCELLED。
        覆盖 invariant 3:engine 语义已完成时 late-cancel 不能改写。"""
        existing = ExecutionEvent(
            event_type=StreamEventType.COMPLETE.value,
            agent_name=None,
            data={"success": True, "response": "real"},
        )
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": [existing]},
        )
        ensure_terminal(pp)
        assert pp.terminal_appended is True
        assert pp.terminal_type == StreamEventType.COMPLETE.value
        # Did NOT append a second terminal
        assert len(pp.final_state["events"]) == 1

    def test_adopts_existing_error_terminal(self):
        existing = ExecutionEvent(
            event_type=StreamEventType.ERROR.value,
            agent_name=None,
            data={"success": False, "error": "engine error"},
        )
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": [existing]},
        )
        ensure_terminal(pp)
        assert pp.terminal_appended is True
        assert pp.terminal_type == StreamEventType.ERROR.value
        assert len(pp.final_state["events"]) == 1

    def test_adopts_existing_cancelled_external_with_reason(self):
        """engine_task 路径 append 的 CANCELLED 带 reason 字段 → 推断 cancel_source=external。
        choose_response 会因此返回 BY_SYSTEM 而非 BY_USER。"""
        existing = ExecutionEvent(
            event_type=StreamEventType.CANCELLED.value,
            agent_name=None,
            data={"cancelled": True, "reason": "external_cancel"},
        )
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": [existing]},
        )
        ensure_terminal(pp)
        assert pp.terminal_type == StreamEventType.CANCELLED.value
        assert pp.cancel_source == "external"

    def test_adopts_existing_cancelled_cooperative_no_reason(self):
        """decide_terminal 走 cooperative 分支时 append 的 CANCELLED 不带 reason →
        推断 cancel_source=cooperative。"""
        existing = ExecutionEvent(
            event_type=StreamEventType.CANCELLED.value,
            agent_name=None,
            data={"cancelled": True, "response": "user msg"},
        )
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": [existing]},
        )
        ensure_terminal(pp)
        assert pp.terminal_type == StreamEventType.CANCELLED.value
        assert pp.cancel_source == "cooperative"

    def test_ignores_historical_terminal_synthesizes_current(self):
        """多轮场景:state["events"] = [parent turn historical, current turn fresh]。
        historical COMPLETE 不算本轮 terminal —— 否则 ensure_terminal adopt 后
        _persist_events 过滤 historical → 本轮 DB 里没有 terminal,EventHistory
        下一轮重建会撞到无终态的半截 turn。"""
        historical_complete = ExecutionEvent(
            event_type=StreamEventType.COMPLETE.value,
            agent_name=None,
            data={"success": True, "response": "previous turn"},
            is_historical=True,
        )
        current_llm = ExecutionEvent(
            event_type=StreamEventType.LLM_COMPLETE.value,
            agent_name="lead_agent",
            data={"content": "current turn partial"},
            is_historical=False,
        )
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": [historical_complete, current_llm]},
        )
        ensure_terminal(pp)
        # 必须合成本轮 external CANCELLED,而不是 adopt historical COMPLETE
        assert pp.terminal_type == StreamEventType.CANCELLED.value
        assert pp.cancel_source == "external"
        # historical + current_llm + 新合成的 CANCELLED
        assert len(pp.final_state["events"]) == 3
        appended = pp.final_state["events"][-1]
        assert appended.event_type == StreamEventType.CANCELLED.value
        assert appended.data["reason"] == "external_cancel_post_processing"
        assert getattr(appended, "is_historical", False) is False, (
            "合成的 terminal 必须是本轮事件(非 historical),否则 _persist_events "
            "会把它过滤掉"
        )

    def test_adopts_current_terminal_among_historical(self):
        """defense-in-depth:historical 段里也有 terminal,但本轮也有了 terminal
        (例如 engine_task cancel handler 已经 append 过 CANCELLED)。adopt 本轮
        的,不要被 historical 干扰。"""
        historical_complete = ExecutionEvent(
            event_type=StreamEventType.COMPLETE.value,
            agent_name=None,
            data={"success": True, "response": "previous turn"},
            is_historical=True,
        )
        current_cancelled = ExecutionEvent(
            event_type=StreamEventType.CANCELLED.value,
            agent_name=None,
            data={"cancelled": True, "reason": "external_cancel"},
            is_historical=False,
        )
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": [historical_complete, current_cancelled]},
        )
        ensure_terminal(pp)
        # adopt 本轮的 CANCELLED,推断 cancel_source=external(有 reason)
        assert pp.terminal_type == StreamEventType.CANCELLED.value
        assert pp.cancel_source == "external"
        # 没有新 append
        assert len(pp.final_state["events"]) == 2

    def test_adopts_existing_timed_out_terminal(self):
        """events 已有本轮 TIMED_OUT(post-processing append 后 cancel 落在 persist 前),
        late-cancel adopt 它而非合成 CANCELLED。cancel_source 留 None,choose_response
        对 TIMED_OUT 不依赖 cancel_source。"""
        existing = ExecutionEvent(
            event_type=StreamEventType.TIMED_OUT.value,
            agent_name=None,
            data={"success": False, "timed_out": True,
                  "response": config.TIMED_OUT_RESPONSE},
        )
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": [existing]},
        )
        ensure_terminal(pp)
        assert pp.terminal_appended is True
        assert pp.terminal_type == StreamEventType.TIMED_OUT.value
        assert pp.cancel_source is None
        # 没有合成第二个 terminal
        assert len(pp.final_state["events"]) == 1

    def test_synthesizes_external_cancelled_when_no_terminal(self):
        """events 里没 terminal → 合成 external CANCELLED 并 append。"""
        pp = PostProcessState(
            conversation_id="c", message_id="m",
            final_state={"events": []},
        )
        ensure_terminal(pp)
        assert pp.terminal_appended is True
        assert pp.terminal_type == StreamEventType.CANCELLED.value
        assert pp.cancel_source == "external"
        assert len(pp.final_state["events"]) == 1
        appended = pp.final_state["events"][0]
        assert appended.event_type == StreamEventType.CANCELLED.value
        assert appended.data["reason"] == "external_cancel_post_processing"


# ============================================================================
# make_external_cancelled_event — 共享 builder,engine_task 和 ensure_terminal 行为对齐
# ============================================================================


class TestMakeExternalCancelledEvent:

    def test_basic_shape(self):
        ev = make_external_cancelled_event(
            conversation_id="conv", message_id="msg", reason="external_cancel",
        )
        assert ev.event_type == StreamEventType.CANCELLED.value
        assert ev.data["success"] is False
        assert ev.data["cancelled"] is True
        assert ev.data["reason"] == "external_cancel"
        assert "execution_metrics" not in ev.data

    def test_with_metrics(self):
        ev = make_external_cancelled_event(
            conversation_id="conv", message_id="msg", reason="external_cancel",
            execution_metrics={"total_duration_ms": 100},
        )
        assert ev.data["execution_metrics"] == {"total_duration_ms": 100}

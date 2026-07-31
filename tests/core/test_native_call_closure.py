import json

import pytest

from core.events import ExecutionEvent, StreamEventType
from core.native_call_closure import (
    NativeCallClosureError,
    assert_native_calls_closed,
    close_open_native_calls,
)


def _event(event_type, data, agent="lead_agent"):
    return ExecutionEvent(event_type=event_type, agent_name=agent, data=data)


def _accepted(*calls):
    return _event(
        StreamEventType.LLM_COMPLETE.value,
        {"content": "", "tool_calls": list(calls)},
    )


def _call(call_id, name="tool", arguments=None):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(
                arguments or {"value": 1}
            ),
        },
    }


def test_closes_never_started_and_interrupted_calls_exactly_once():
    state = {
        "events": [
            _accepted(_call("call_1", "a"), _call("call_2", "b")),
            _event(
                StreamEventType.TOOL_START.value,
                {"call_id": "call_2", "tool": "b", "params": {"value": 1}},
            ),
        ]
    }

    generated = close_open_native_calls(state, "execution was cancelled")

    assert [event.event_type for event in generated] == [
        StreamEventType.TOOL_START.value,
        StreamEventType.TOOL_COMPLETE.value,
        StreamEventType.TOOL_COMPLETE.value,
    ]
    first_start = generated[0].data
    assert first_start["call_id"] == "call_1"
    assert first_start["params"] == {"value": 1}
    assert first_start["reason"] == "模型请求调用 a"
    interrupted = generated[-1].data
    assert interrupted["call_id"] == "call_2"
    assert "side effects may or may not" in interrupted["error"]
    assert_native_calls_closed(state)
    assert close_open_native_calls(state, "execution was cancelled") == []


def test_existing_completion_is_not_duplicated():
    state = {
        "events": [
            _accepted(_call("call_1")),
            _event(StreamEventType.TOOL_START.value, {"call_id": "call_1"}),
            _event(StreamEventType.TOOL_COMPLETE.value, {"call_id": "call_1"}),
        ]
    }

    assert close_open_native_calls(state, "execution ended") == []
    assert_native_calls_closed(state)


def test_duplicate_accepted_ids_fail_loudly():
    state = {"events": [_accepted(_call("call_1"), _call("call_1", "other"))]}

    with pytest.raises(NativeCallClosureError, match="not unique"):
        close_open_native_calls(state, "execution ended")


def test_assertion_rejects_an_open_accepted_call():
    state = {"events": [_accepted(_call("call_open"))]}

    with pytest.raises(NativeCallClosureError, match="not closed exactly once"):
        assert_native_calls_closed(state)

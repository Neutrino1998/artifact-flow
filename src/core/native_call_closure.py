"""Structural closure for accepted native tool calls."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from core.agent_runtime import StopReason
from core.events import ExecutionEvent, StreamEventType


class NativeCallClosureError(RuntimeError):
    """Persisted native call/result structure would be ambiguous or orphaned."""


def terminal_reason_for_stop(stop_reason: StopReason) -> str:
    if stop_reason is StopReason.TIMEOUT:
        return "execution timed out"
    if stop_reason in {
        StopReason.COOPERATIVE_CANCEL,
        StopReason.EXTERNAL_CANCEL,
    }:
        return "execution was cancelled"
    if stop_reason is StopReason.ERROR:
        return "execution ended with an error"
    return "execution ended before this call completed"


def close_open_native_calls(
    state: Dict[str, Any], terminal_reason: str
) -> List[ExecutionEvent]:
    """Append exactly one failure result for every still-open accepted call.

    The helper is idempotent: a second invocation observes the synthetic
    completions and returns no events.
    """
    events = [
        event for event in state.get("events", [])
        if not getattr(event, "is_historical", False)
    ]
    accepted: list[tuple[str, str | None, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for event in events:
        if event.event_type != StreamEventType.LLM_COMPLETE.value:
            continue
        for call in (event.data or {}).get("tool_calls") or []:
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise NativeCallClosureError("accepted native tool call has no id")
            if call_id in seen_ids:
                raise NativeCallClosureError(
                    f"accepted native tool call id {call_id!r} is not unique within the turn"
                )
            seen_ids.add(call_id)
            accepted.append((call_id, event.agent_name, call))

    starts: dict[str, int] = {}
    completes: dict[str, int] = {}
    for event in events:
        data = event.data or {}
        call_id = data.get("call_id")
        if event.event_type == StreamEventType.TOOL_START.value and call_id:
            starts[call_id] = starts.get(call_id, 0) + 1
        elif event.event_type == StreamEventType.TOOL_COMPLETE.value and call_id:
            completes[call_id] = completes.get(call_id, 0) + 1

    generated: List[ExecutionEvent] = []
    for call_id, agent_name, call in accepted:
        complete_count = completes.get(call_id, 0)
        if complete_count > 1:
            raise NativeCallClosureError(
                f"native tool call {call_id!r} has {complete_count} completion events"
            )
        if complete_count == 1:
            continue
        start_count = starts.get(call_id, 0)
        if start_count > 1:
            raise NativeCallClosureError(
                f"native tool call {call_id!r} has {start_count} start events"
            )

        function = call.get("function") or {}
        tool_name = str(function.get("name") or "unknown")
        params, reason = _display_arguments(function.get("arguments"), tool_name)
        if start_count == 0:
            generated.append(ExecutionEvent(
                event_type=StreamEventType.TOOL_START.value,
                agent_name=agent_name,
                data={
                    "call_id": call_id,
                    "tool": tool_name,
                    "params": params,
                    "reason": reason,
                    "synthetic": True,
                },
            ))
            error = f"Tool was not run because {terminal_reason}."
        else:
            error = (
                f"Tool execution was interrupted because {terminal_reason}; "
                "side effects may or may not have been applied."
            )
        generated.append(ExecutionEvent(
            event_type=StreamEventType.TOOL_COMPLETE.value,
            agent_name=agent_name,
            data={
                "call_id": call_id,
                "tool": tool_name,
                "success": False,
                "error": error,
                "duration_ms": 0,
                "synthetic": True,
            },
        ))

    state.setdefault("events", []).extend(generated)
    return generated


def assert_native_calls_closed(state: Dict[str, Any]) -> None:
    events = [
        event for event in state.get("events", [])
        if not getattr(event, "is_historical", False)
    ]
    accepted: list[str] = []
    completions: dict[str, int] = {}
    for event in events:
        data = event.data or {}
        if event.event_type == StreamEventType.LLM_COMPLETE.value:
            accepted.extend(
                call.get("id") for call in (data.get("tool_calls") or [])
            )
        elif event.event_type == StreamEventType.TOOL_COMPLETE.value:
            call_id = data.get("call_id")
            if call_id:
                completions[call_id] = completions.get(call_id, 0) + 1
    if any(not isinstance(call_id, str) or not call_id for call_id in accepted):
        raise NativeCallClosureError("accepted native tool call has no id")
    if len(accepted) != len(set(accepted)):
        raise NativeCallClosureError("accepted native tool call ids are not unique within the turn")
    bad = {
        call_id: completions.get(call_id, 0)
        for call_id in accepted
        if completions.get(call_id, 0) != 1
    }
    if bad:
        raise NativeCallClosureError(f"native tool calls are not closed exactly once: {bad}")


def _display_arguments(raw: Any, tool_name: str) -> tuple[dict[str, Any], str]:
    params: dict[str, Any] = {}
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                params = dict(decoded)
        except json.JSONDecodeError:
            pass
    return params, f"模型请求调用 {tool_name}"

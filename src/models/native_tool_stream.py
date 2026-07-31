"""Provider-neutral assembly of streamed OpenAI-style native tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


ACCEPTING_FINISH_REASONS = frozenset({"stop", "tool_calls", "function_call"})


class NativeToolStreamError(ValueError):
    """A streamed native tool-call envelope was truncated or inconsistent."""


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _append_delta(current: str, fragment: Any) -> str:
    """Append one LiteLLM/OpenAI stream delta without inspecting its content."""
    if fragment is None:
        return current
    return current + str(fragment)


@dataclass
class _PartialNativeToolCall:
    index: int
    call_id: str = ""
    call_type: str = "function"
    name: str = ""
    arguments: str = ""

    def add(self, delta: Any) -> None:
        incoming_id = _get(delta, "id")
        if incoming_id:
            incoming = str(incoming_id)
            if self.call_id and incoming != self.call_id:
                raise NativeToolStreamError(
                    f"conflicting tool call ids at index {self.index}: "
                    f"{self.call_id!r} vs {incoming!r}"
                )
            self.call_id = incoming

        incoming_type = _get(delta, "type")
        if incoming_type:
            incoming_type = str(incoming_type)
            if self.call_type and incoming_type != self.call_type:
                raise NativeToolStreamError(
                    f"conflicting tool call types at index {self.index}: "
                    f"{self.call_type!r} vs {incoming_type!r}"
                )
            self.call_type = incoming_type

        function = _get(delta, "function") or {}
        self.name = _append_delta(self.name, _get(function, "name"))
        self.arguments = _append_delta(
            self.arguments, _get(function, "arguments")
        )

    def complete(self) -> dict[str, Any]:
        if not self.call_id:
            raise NativeToolStreamError(
                f"tool call at index {self.index} has no id"
            )
        if self.call_type != "function":
            raise NativeToolStreamError(
                f"tool call {self.call_id!r} has unsupported type {self.call_type!r}"
            )
        if not self.name:
            raise NativeToolStreamError(
                f"tool call {self.call_id!r} has no function name"
            )
        return {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }


class NativeToolCallAssembler:
    """Buffer deltas until a normal terminal chunk accepts the envelope."""

    def __init__(self) -> None:
        self._calls: dict[int, _PartialNativeToolCall] = {}
        self.saw_delta = False

    def add_many(self, deltas: Iterable[Any]) -> None:
        for delta in deltas:
            self.saw_delta = True
            index = _get(delta, "index")
            if not isinstance(index, int) or isinstance(index, bool):
                raise NativeToolStreamError(
                    f"tool call delta has invalid index: {index!r}"
                )
            partial = self._calls.setdefault(
                index, _PartialNativeToolCall(index=index)
            )
            partial.add(delta)

    def accept(self, finish_reasons: Iterable[str]) -> list[dict[str, Any]]:
        reasons = list(finish_reasons)
        if not self.saw_delta:
            return []
        if not reasons:
            raise NativeToolStreamError(
                "tool-call stream ended without a terminal finish reason"
            )
        rejected = [reason for reason in reasons if reason not in ACCEPTING_FINISH_REASONS]
        if rejected:
            raise NativeToolStreamError(
                f"tool-call stream ended with non-accepting finish reason(s): {rejected!r}"
            )

        calls = [self._calls[index].complete() for index in sorted(self._calls)]
        ids = [call["id"] for call in calls]
        if len(ids) != len(set(ids)):
            raise NativeToolStreamError(f"duplicate tool call id(s): {ids!r}")
        return calls

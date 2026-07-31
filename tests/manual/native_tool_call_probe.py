#!/usr/bin/env python3
"""Independent native tool-call protocol probe for candidate providers.

This is deliberately a manual diagnostic, not a second ArtifactFlow LLM
adapter.  It calls the project-pinned LiteLLM package directly and records a
sanitized structural report that can be turned into codec fixtures later.

Run from the repository root in a Python 3.11 environment built from
``requirements.lock``::

    python -m tests.manual.native_tool_call_probe --models all
    python -m tests.manual.native_tool_call_probe \
        --models qwen3.7-plus deepseek-v4-flash --skip-vision

The complete reasoning text is retained only in memory long enough to replay
the assistant envelope.  Reports contain lengths and hashes, never reasoning
text, API keys, or image data URIs.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# LiteLLM otherwise tries to fetch the cost map during import, which is both
# unnecessary for this probe and unsafe for air-gapped deployments.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import litellm
from litellm import acompletion

litellm.suppress_debug_info = True


@dataclass(frozen=True)
class Candidate:
    alias: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)
    vision: bool = False


CANDIDATES: dict[str, Candidate] = {
    "qwen3.7-plus": Candidate(
        alias="qwen3.7-plus",
        model="dashscope/qwen3.7-plus",
        params={
            "enable_thinking": True,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
        },
        vision=True,
    ),
    "deepseek-v4-flash": Candidate(
        alias="deepseek-v4-flash",
        model="dashscope/deepseek-v4-flash",
    ),
    "glm-5.2": Candidate(alias="glm-5.2", model="dashscope/glm-5.2"),
    "kimi-k2.6": Candidate(
        alias="kimi-k2.6",
        model="dashscope/kimi-k2.6",
        vision=True,
    ),
    "MiniMax-M2.5": Candidate(
        alias="MiniMax-M2.5",
        model="dashscope/MiniMax-M2.5",
    ),
}


_SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_DATA_URI_RE = re.compile(r"data:image/[^;,\s]+;base64,[A-Za-z0-9+/=]+")
_NORMAL_FINISH_REASONS = {None, "stop", "tool_calls", "function_call"}


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=False)
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _text_fingerprint(value: str | None) -> dict[str, Any]:
    text = value or ""
    return {
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
    }


def redact_error(value: Any) -> str:
    text = str(value)
    key = os.getenv("DASHSCOPE_API_KEY")
    if key:
        text = text.replace(key, "<redacted-api-key>")
    text = _SECRET_RE.sub("sk-<redacted>", text)
    text = _DATA_URI_RE.sub("data:image/<redacted>", text)
    return text[:4000]


def _append_delta(current: str, fragment: Any) -> str:
    """Append one LiteLLM/OpenAI stream delta without content heuristics."""
    if fragment is None:
        return current
    return current + str(fragment)


@dataclass
class PartialToolCall:
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
                raise ValueError(
                    f"conflicting tool call ids at index {self.index}: "
                    f"{self.call_id!r} vs {incoming!r}"
                )
            self.call_id = incoming
        incoming_type = _get(delta, "type")
        if incoming_type:
            incoming_type = str(incoming_type)
            if self.call_type and incoming_type != self.call_type:
                raise ValueError(
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
            raise ValueError(f"tool call at index {self.index} has no id")
        if not self.name:
            raise ValueError(f"tool call {self.call_id!r} has no function name")
        try:
            decoded = json.loads(self.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"tool call {self.call_id!r} has invalid JSON arguments: {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise ValueError(
                f"tool call {self.call_id!r} arguments must decode to an object"
            )
        return {
            "index": self.index,
            "id": self.call_id,
            "type": self.call_type,
            "function": {"name": self.name, "arguments": self.arguments},
            "decoded_arguments": decoded,
        }


class ToolCallAssembler:
    """Small probe-only assembler used to observe provider chunk behavior."""

    def __init__(self) -> None:
        self._calls: dict[int, PartialToolCall] = {}
        self.saw_delta = False

    def add_many(self, deltas: Iterable[Any]) -> None:
        for delta in deltas:
            self.saw_delta = True
            index = _get(delta, "index")
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError(f"tool call delta has invalid index: {index!r}")
            partial = self._calls.setdefault(index, PartialToolCall(index=index))
            partial.add(delta)

    def complete(self) -> list[dict[str, Any]]:
        calls = [self._calls[index].complete() for index in sorted(self._calls)]
        ids = [call["id"] for call in calls]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate tool call id(s): {ids!r}")
        return calls


def _usage_dict(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    data = _dump(value)
    return {
        "prompt_tokens": data.get("prompt_tokens"),
        "completion_tokens": data.get("completion_tokens"),
        "total_tokens": data.get("total_tokens"),
    }


def _tool_delta_shape(delta: Any) -> dict[str, Any]:
    function = _get(delta, "function") or {}
    return {
        "index": _get(delta, "index"),
        "id_fragment": _get(delta, "id"),
        "type": _get(delta, "type"),
        "function": {
            "name_fragment": _get(function, "name"),
            # Probe arguments are synthetic and intentionally retained so stage 1
            # can turn real fragmentation into deterministic codec fixtures.
            "arguments_fragment": _get(function, "arguments"),
        },
    }


def sanitized_chunk_shape(chunk: Any, sequence: int) -> dict[str, Any]:
    dumped = _dump(chunk)
    choices = _get(chunk, "choices") or []
    shaped_choices = []
    for choice in choices:
        delta = _get(choice, "delta") or {}
        delta_dump = _dump(delta)
        content = _get(delta, "content")
        reasoning = _get(delta, "reasoning_content")
        shaped_choices.append({
            "index": _get(choice, "index"),
            "finish_reason": _get(choice, "finish_reason"),
            "delta_keys": sorted(delta_dump),
            "content_chars": len(content) if isinstance(content, str) else 0,
            "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
            "tool_calls": [
                _tool_delta_shape(item)
                for item in (_get(delta, "tool_calls") or [])
            ],
        })
    return {
        "sequence": sequence,
        "chunk_type": type(chunk).__name__,
        "top_level_keys": sorted(dumped),
        "choices": shaped_choices,
        "usage": _usage_dict(_get(chunk, "usage")),
    }


def summarize_chunk_shapes(shapes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Keep protocol-significant chunks and count repetitive stream shapes."""
    content_chars = 0
    reasoning_chars = 0
    notable_chunks: list[dict[str, Any]] = []
    variants: dict[str, dict[str, Any]] = {}

    for shape in shapes:
        choices = shape["choices"]
        content_chars += sum(choice["content_chars"] for choice in choices)
        reasoning_chars += sum(choice["reasoning_chars"] for choice in choices)
        has_tool_delta = any(choice["tool_calls"] for choice in choices)
        has_finish = any(choice["finish_reason"] is not None for choice in choices)
        if has_tool_delta or has_finish or shape["usage"] is not None:
            notable_chunks.append(shape)

        variant = {
            "chunk_type": shape["chunk_type"],
            "top_level_keys": shape["top_level_keys"],
            "choices": [
                {
                    "index": choice["index"],
                    "finish_reason": choice["finish_reason"],
                    "delta_keys": choice["delta_keys"],
                    "has_content": choice["content_chars"] > 0,
                    "has_reasoning": choice["reasoning_chars"] > 0,
                    "tool_call_count": len(choice["tool_calls"]),
                }
                for choice in choices
            ],
            "usage_present": shape["usage"] is not None,
        }
        key = json.dumps(variant, ensure_ascii=True, sort_keys=True)
        if key not in variants:
            variants[key] = {"count": 0, "shape": variant}
        variants[key]["count"] += 1

    return {
        "total": len(shapes),
        "content_chars": content_chars,
        "reasoning_chars": reasoning_chars,
        "structural_variants": list(variants.values()),
        "notable_chunks": notable_chunks,
    }


def sanitized_message_structure(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        item: dict[str, Any] = {"role": message.get("role")}
        if isinstance(content, list):
            blocks = []
            for block in content:
                block_type = block.get("type") if isinstance(block, dict) else None
                if block_type == "image_url":
                    url = ((block.get("image_url") or {}).get("url") or "")
                    blocks.append({
                        "type": "image_url",
                        "data_uri": url.startswith("data:image/"),
                        "chars": len(url),
                    })
                elif block_type == "text":
                    blocks.append({
                        "type": "text",
                        **_text_fingerprint(block.get("text")),
                    })
                else:
                    blocks.append({"type": block_type or "unknown"})
            item["content"] = {"kind": "blocks", "blocks": blocks}
        else:
            item["content"] = {"kind": "text", **_text_fingerprint(content)}

        if "reasoning_content" in message:
            item["reasoning_content"] = _text_fingerprint(
                message.get("reasoning_content")
            )
        if message.get("tool_calls"):
            item["tool_calls"] = [
                {
                    "id": call.get("id"),
                    "type": call.get("type"),
                    "name": (call.get("function") or {}).get("name"),
                    "arguments": (call.get("function") or {}).get("arguments"),
                }
                for call in message["tool_calls"]
            ]
        if message.get("tool_call_id"):
            item["tool_call_id"] = message["tool_call_id"]
        out.append(item)
    return out


@dataclass
class CallCapture:
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reasons: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    chunk_shapes: list[dict[str, Any]] = field(default_factory=list)
    request_structure: list[dict[str, Any]] = field(default_factory=list)
    protocol_errors: list[str] = field(default_factory=list)

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content or None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": call["type"],
                    "function": {
                        "name": call["function"]["name"],
                        "arguments": call["function"]["arguments"],
                    },
                }
                for call in self.tool_calls
            ],
        }
        if self.reasoning_content:
            message["reasoning_content"] = self.reasoning_content
        return message

    def report(self) -> dict[str, Any]:
        return {
            "request_messages": self.request_structure,
            "response": {
                "content": _text_fingerprint(self.content),
                "reasoning_content": _text_fingerprint(self.reasoning_content),
                "tool_calls": self.tool_calls,
                "finish_reasons": self.finish_reasons,
                "usage": self.usage,
                "protocol_errors": self.protocol_errors,
            },
            "chunks": summarize_chunk_shapes(self.chunk_shapes),
        }


async def stream_call(
    *,
    candidate: Candidate,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    timeout: float,
) -> CallCapture:
    capture = CallCapture(request_structure=sanitized_message_structure(messages))
    assembler = ToolCallAssembler()
    params: dict[str, Any] = {
        "model": candidate.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": timeout,
        **candidate.params,
    }
    if tools:
        params["tools"] = tools

    response = await acompletion(**params)
    sequence = 0
    try:
        async for chunk in response:
            capture.chunk_shapes.append(sanitized_chunk_shape(chunk, sequence))
            sequence += 1
            usage = _usage_dict(_get(chunk, "usage"))
            if usage:
                capture.usage = usage
            for choice in (_get(chunk, "choices") or []):
                finish_reason = _get(choice, "finish_reason")
                if finish_reason is not None:
                    capture.finish_reasons.append(str(finish_reason))
                delta = _get(choice, "delta") or {}
                content = _get(delta, "content")
                if isinstance(content, str):
                    capture.content += content
                reasoning = _get(delta, "reasoning_content")
                if isinstance(reasoning, str):
                    capture.reasoning_content += reasoning
                tool_deltas = _get(delta, "tool_calls") or []
                if tool_deltas:
                    try:
                        assembler.add_many(tool_deltas)
                    except ValueError as exc:
                        capture.protocol_errors.append(str(exc))
    except Exception as exc:
        # Preserve the chunks already observed for diagnosis, but never promote
        # buffered deltas from a failed/truncated stream into accepted calls.
        capture.protocol_errors.append(f"stream failed: {redact_error(exc)}")
        return capture

    if assembler.saw_delta:
        if any(reason not in _NORMAL_FINISH_REASONS for reason in capture.finish_reasons):
            capture.protocol_errors.append(
                f"tool-call stream ended with non-accepting finish reason(s): "
                f"{capture.finish_reasons!r}"
            )
        if not capture.protocol_errors:
            try:
                capture.tool_calls = assembler.complete()
            except ValueError as exc:
                capture.protocol_errors.append(str(exc))
    return capture


def _function_tool(name: str, description: str, value_name: str = "query") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    value_name: {
                        "type": "string",
                        "description": f"Synthetic probe {value_name}.",
                    },
                },
                "required": [value_name],
                "additionalProperties": False,
            },
        },
    }


def _result_message(call: dict[str, Any], data: str) -> dict[str, Any]:
    name = call["function"]["name"]
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "content": (
            f'<tool_result name="{name}" success="true">\n'
            f"<data>\n{data}\n</data>\n"
            "</tool_result>"
        ),
    }


def _validate_calls(
    capture: CallCapture,
    *,
    allowed_names: set[str],
    require_any: bool,
) -> list[str]:
    errors = list(capture.protocol_errors)
    if require_any and not capture.tool_calls:
        errors.append("model did not produce a native tool call")
    for call in capture.tool_calls:
        if call["function"]["name"] not in allowed_names:
            errors.append(
                f"unexpected function name {call['function']['name']!r}; "
                f"allowed={sorted(allowed_names)!r}"
            )
    return errors


async def run_minimal(candidate: Candidate, timeout: float) -> dict[str, Any]:
    tools = [_function_tool("probe_lookup", "Return one synthetic probe value.")]
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": (
            "Call probe_lookup exactly once with query='native-protocol'. "
            "Do not answer from memory."
        ),
    }]
    calls: list[dict[str, Any]] = []
    observations: list[str] = []
    try:
        first = await stream_call(
            candidate=candidate, messages=messages, tools=tools, timeout=timeout
        )
        calls.append(first.report())
        errors = _validate_calls(
            first, allowed_names={"probe_lookup"}, require_any=True
        )
        if errors:
            return {"status": "fail", "errors": errors, "observations": observations, "calls": calls}

        messages.append(first.assistant_message())
        messages.extend(
            _result_message(call, "synthetic native protocol result: 37")
            for call in first.tool_calls
        )
        messages.append({
            "role": "user",
            "content": (
                "<system-reminder>Consume the completed tool result and reply "
                "with PROBE_OK and the number. Do not call another tool.</system-reminder>"
            ),
        })
        second = await stream_call(
            candidate=candidate, messages=messages, tools=tools, timeout=timeout
        )
        calls.append(second.report())
        if second.protocol_errors:
            errors.extend(second.protocol_errors)
        if not second.content and not second.tool_calls:
            errors.append("follow-up response had neither content nor tool calls")
        return {
            "status": "fail" if errors else "pass",
            "errors": errors,
            "observations": observations,
            "calls": calls,
        }
    except Exception as exc:
        return {
            "status": "fail",
            "errors": [redact_error(exc)],
            "observations": observations,
            "calls": calls,
        }


async def run_multi_content(candidate: Candidate, timeout: float) -> dict[str, Any]:
    tools = [
        _function_tool("probe_alpha", "Return synthetic alpha.", "value"),
        _function_tool("probe_beta", "Return synthetic beta.", "value"),
    ]
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": (
            "First write a very short visible preface, then in the same assistant "
            "turn call probe_alpha with value='A' and probe_beta with value='B'. "
            "Use one native call for each value."
        ),
    }]
    calls: list[dict[str, Any]] = []
    observations: list[str] = []
    try:
        first = await stream_call(
            candidate=candidate, messages=messages, tools=tools, timeout=timeout
        )
        calls.append(first.report())
        errors = list(first.protocol_errors)
        if errors:
            return {
                "status": "fail",
                "errors": errors,
                "observations": observations,
                "calls": calls,
            }
        if not first.tool_calls:
            return {
                "status": "pass",
                "errors": [],
                "observations": ["model did not produce the optional multi-call shape"],
                "calls": calls,
            }
        # This optional scenario probes wire shape and replay, not instruction
        # adherence. A structurally valid call to an undeclared name is a
        # model-behavior observation: the runtime can return a bound failure
        # result, so replay that same envelope here.
        allowed_names = {"probe_alpha", "probe_beta"}
        for call in first.tool_calls:
            if call["function"]["name"] not in allowed_names:
                observations.append(
                    f"model called undeclared function "
                    f"{call['function']['name']!r}"
                )
        observations.extend(_reason_observations(first.tool_calls))
        if len(first.tool_calls) < 2:
            observations.append("model produced tool calls, but not a same-turn multi-call")
        if not first.content:
            observations.append("model produced tool calls without visible content")
        if errors:
            return {"status": "fail", "errors": errors, "observations": observations, "calls": calls}

        messages.append(first.assistant_message())
        messages.extend(
            _result_message(call, f"result for {call['function']['name']}")
            for call in first.tool_calls
        )
        messages.append({
            "role": "user",
            "content": "<system-reminder>Reply MULTI_REPLAY_OK without tools.</system-reminder>",
        })
        second = await stream_call(
            candidate=candidate, messages=messages, tools=tools, timeout=timeout
        )
        calls.append(second.report())
        errors.extend(second.protocol_errors)
        return {
            "status": "fail" if errors else "pass",
            "errors": errors,
            "observations": observations,
            "calls": calls,
        }
    except Exception as exc:
        return {
            "status": "fail",
            "errors": [redact_error(exc)],
            "observations": observations,
            "calls": calls,
        }


def _number_png_data_uri(text: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    small = Image.new("RGB", (96, 48), "white")
    draw = ImageDraw.Draw(small)
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (small.width - (bbox[2] - bbox[0])) // 2
    y = (small.height - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), text, fill="black", font=font)
    image = small.resize((768, 384), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _vision_carrier(call_id: str, numbers: Sequence[str]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for index, number in enumerate(numbers, 1):
        blocks.append({
            "type": "text",
            "text": (
                f'<tool_image tool_call_id="{call_id}" '
                f'artifact_id="probe-image-{index}" version="1" '
                'content_type="image/png">'
            ),
        })
        blocks.append({
            "type": "image_url",
            "image_url": {"url": _number_png_data_uri(number)},
        })
    blocks.append({
        "type": "text",
        "text": (
            "<system-reminder>Read every image above and answer only with the "
            "numbers in image order, comma-separated. Do not call a tool.</system-reminder>"
        ),
    })
    return {"role": "user", "content": blocks}


async def run_vision(
    candidate: Candidate,
    timeout: float,
    numbers: Sequence[str],
) -> dict[str, Any]:
    scenario = "vision_single" if len(numbers) == 1 else "vision_multi"
    tools = [
        _function_tool(
            "probe_prepare_images",
            "Prepare synthetic images for the native carrier probe.",
            "count",
        )
    ]
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": (
            f"Call probe_prepare_images exactly once with count='{len(numbers)}'. "
            "Wait for the image carrier."
        ),
    }]
    calls: list[dict[str, Any]] = []
    observations: list[str] = []
    try:
        first = await stream_call(
            candidate=candidate, messages=messages, tools=tools, timeout=timeout
        )
        calls.append(first.report())
        errors = _validate_calls(
            first, allowed_names={"probe_prepare_images"}, require_any=True
        )
        if errors:
            return {"name": scenario, "status": "fail", "errors": errors, "observations": observations, "calls": calls}

        messages.append(first.assistant_message())
        messages.extend(
            _result_message(call, f"prepared {len(numbers)} synthetic image(s)")
            for call in first.tool_calls
        )
        messages.append(_vision_carrier(first.tool_calls[0]["id"], numbers))
        second = await stream_call(
            candidate=candidate, messages=messages, tools=tools, timeout=timeout
        )
        calls.append(second.report())
        errors.extend(second.protocol_errors)
        if not second.content and not second.tool_calls:
            errors.append("vision carrier follow-up had neither content nor tool calls")
        positions = [second.content.find(number) for number in numbers]
        if any(position < 0 for position in positions):
            observations.append(
                f"vision content mismatch; expected={list(numbers)!r}, "
                f"received={redact_error(second.content)!r}"
            )
        elif positions != sorted(positions):
            observations.append(
                "vision response contained expected numbers in the wrong order"
            )
        return {
            "name": scenario,
            "status": "fail" if errors else "pass",
            "errors": errors,
            "observations": observations,
            "calls": calls,
        }
    except Exception as exc:
        return {
            "name": scenario,
            "status": "fail",
            "errors": [redact_error(exc)],
            "observations": observations,
            "calls": calls,
        }


async def run_candidate(
    candidate: Candidate,
    *,
    timeout: float,
    skip_vision: bool,
) -> dict[str, Any]:
    print(f"[{candidate.alias}] minimal text loop", flush=True)
    minimal = await run_minimal(candidate, timeout)
    print(f"[{candidate.alias}] minimal={minimal['status']}", flush=True)

    print(f"[{candidate.alias}] optional multi/content shape", flush=True)
    multi = await run_multi_content(candidate, timeout)
    print(f"[{candidate.alias}] multi_content={multi['status']}", flush=True)

    scenarios: dict[str, Any] = {
        "minimal_text_loop": minimal,
        "multi_content": multi,
    }
    if candidate.vision and not skip_vision:
        for numbers in (("42",), ("17", "29")):
            result = await run_vision(candidate, timeout, numbers)
            scenarios[result.pop("name")] = result
            print(
                f"[{candidate.alias}] "
                f"{'vision_single' if len(numbers) == 1 else 'vision_multi'}="
                f"{result['status']}",
                flush=True,
            )

    # Absence of the optional multi-call/content shape is not a failure. Once a
    # provider produces calls, their wire shape must assemble and replay; model
    # instruction adherence remains an observation rather than a protocol gate.
    gate_names = ["minimal_text_loop", "multi_content"]
    if candidate.vision and not skip_vision:
        gate_names.extend(["vision_single", "vision_multi"])
    gate_passed = all(scenarios[name]["status"] == "pass" for name in gate_names)
    return {
        "alias": candidate.alias,
        "model": candidate.model,
        "vision_declared": candidate.vision,
        "gate_passed": gate_passed,
        "scenarios": scenarios,
    }


def _git_commit() -> str | None:
    injected = os.getenv("ARTIFACTFLOW_PROBE_GIT_COMMIT")
    if injected:
        return injected
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return None


def _resolve_candidates(names: Sequence[str]) -> list[Candidate]:
    if not names or names == ["all"]:
        return list(CANDIDATES.values())
    unknown = [name for name in names if name not in CANDIDATES]
    if unknown:
        raise ValueError(
            f"unknown candidate(s) {unknown!r}; available={list(CANDIDATES)!r}"
        )
    return [CANDIDATES[name] for name in names]


async def async_main(args: argparse.Namespace) -> int:
    candidates = _resolve_candidates(args.models)
    if not os.getenv("DASHSCOPE_API_KEY"):
        raise RuntimeError("DASHSCOPE_API_KEY is not configured")

    started = datetime.now(timezone.utc)
    results = []
    for candidate in candidates:
        results.append(
            await run_candidate(
                candidate,
                timeout=args.timeout,
                skip_vision=args.skip_vision,
            )
        )

    finished = datetime.now(timezone.utc)
    report = {
        "format": "artifactflow-native-tool-call-probe",
        "version": 1,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "environment": {
            "git_commit": _git_commit(),
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "requirements_lock_sha256": hashlib.sha256(
                (ROOT / "requirements.lock").read_bytes()
            ).hexdigest(),
            "python": platform.python_version(),
            "litellm": importlib.metadata.version("litellm"),
            "platform": platform.platform(),
        },
        "policy": {
            "full_reasoning_persisted": False,
            "api_keys_persisted": False,
            "image_data_persisted": False,
            "usage_missing_is_gate_failure": False,
            "missing_reason_is_gate_failure": False,
            "optional_multi_shape_absence_is_gate_failure": False,
            "optional_multi_protocol_failure_is_gate_failure": True,
            "unexpected_optional_function_is_gate_failure": False,
            "vision_content_accuracy_is_gate_failure": False,
            "vision_carrier_rejection_is_gate_failure": True,
        },
        "results": results,
    }

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"sanitized report: {output}")
    failed = [result["alias"] for result in results if not result["gate_passed"]]
    if failed:
        print(f"protocol gate failed: {', '.join(failed)}")
        return 1
    print("protocol gate passed for all selected candidates")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        nargs="+",
        default=["all"],
        help=f"Candidate aliases or 'all'. Available: {', '.join(CANDIDATES)}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / "native-tool-call-probe.json",
        help="Sanitized JSON report path (default: tmp/native-tool-call-probe.json).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Per-request timeout in seconds (default: 180).",
    )
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Skip Qwen/Kimi single-image and multi-image carrier gates.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    try:
        code = asyncio.run(async_main(args))
    except (ValueError, RuntimeError) as exc:
        print(f"error: {redact_error(exc)}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()

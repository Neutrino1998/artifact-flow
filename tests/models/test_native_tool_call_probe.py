import json

import pytest

import tests.manual.native_tool_call_probe as probe

from tests.manual.native_tool_call_probe import (
    Candidate,
    CallCapture,
    ToolCallAssembler,
    _vision_carrier,
    redact_error,
    summarize_chunk_shapes,
    sanitized_message_structure,
)


def _delta(index, *, call_id=None, name=None, arguments=None):
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def test_probe_assembler_handles_split_and_cumulative_deltas():
    assembler = ToolCallAssembler()
    assembler.add_many([
        _delta(0, call_id="call_a", name="probe_", arguments='{"query":'),
        _delta(1, call_id="call_b", name="probe_beta", arguments='{"value":"B"}'),
        _delta(0, name="alpha", arguments='"A"}'),
        # Some provider adapters repeat a cumulative arguments value.
        _delta(1, call_id="call_b", arguments='{"value":"B"}'),
    ])

    calls = assembler.complete()

    assert [call["id"] for call in calls] == ["call_a", "call_b"]
    assert calls[0]["function"] == {
        "name": "probe_alpha",
        "arguments": '{"query":"A"}',
    }
    assert calls[0]["decoded_arguments"] == {"query": "A"}
    assert calls[1]["decoded_arguments"] == {"value": "B"}


def test_probe_assembler_rejects_missing_index_and_conflicting_ids():
    assembler = ToolCallAssembler()
    with pytest.raises(ValueError, match="invalid index"):
        assembler.add_many([_delta(None, call_id="call_a", name="probe")])

    assembler.add_many([_delta(0, call_id="call_a", name="probe", arguments="{}")])
    with pytest.raises(ValueError, match="conflicting tool call ids"):
        assembler.add_many([_delta(0, call_id="call_b")])


def test_capture_report_never_contains_reasoning_text():
    capture = CallCapture(
        content="visible response",
        reasoning_content="private chain of thought must not persist",
        tool_calls=[{
            "index": 0,
            "id": "call_a",
            "type": "function",
            "function": {"name": "probe", "arguments": '{"__reason":"safe"}'},
            "decoded_arguments": {"__reason": "safe"},
        }],
    )

    serialized = json.dumps(capture.report())

    assert "private chain of thought" not in serialized
    assert capture.report()["response"]["reasoning_content"]["chars"] == 41


def test_message_structure_redacts_image_data_and_reasoning():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "hidden reasoning",
            "tool_calls": [],
        },
        _vision_carrier("call_image", ["42"]),
    ]

    serialized = json.dumps(sanitized_message_structure(messages))

    assert "hidden reasoning" not in serialized
    assert "data:image/png;base64" not in serialized
    assert '"data_uri": true' in serialized


def test_chunk_summary_keeps_tool_fragments_and_compacts_repeated_shapes():
    base = {
        "chunk_type": "ModelResponseStream",
        "top_level_keys": ["choices"],
        "choices": [{
            "index": 0,
            "finish_reason": None,
            "delta_keys": ["content", "reasoning_content", "tool_calls"],
            "content_chars": 0,
            "reasoning_chars": 3,
            "tool_calls": [],
        }],
        "usage": None,
    }
    tool = json.loads(json.dumps(base))
    tool["sequence"] = 2
    tool["choices"][0]["reasoning_chars"] = 0
    tool["choices"][0]["tool_calls"] = [{
        "index": 0,
        "id_fragment": "call_a",
        "type": "function",
        "function": {"name_fragment": "probe", "arguments_fragment": "{}"},
    }]
    shapes = [
        {"sequence": 0, **base},
        {"sequence": 1, **base},
        tool,
    ]

    summary = summarize_chunk_shapes(shapes)

    assert summary["total"] == 3
    assert summary["reasoning_chars"] == 6
    assert len(summary["structural_variants"]) == 2
    assert summary["structural_variants"][0]["count"] == 2
    assert summary["notable_chunks"] == [tool]


def test_error_redaction_covers_api_keys_and_data_uris(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-super-secret-value")
    value = (
        "request failed for sk-super-secret-value with "
        "data:image/png;base64,QUJDRA=="
    )

    redacted = redact_error(value)

    assert "super-secret" not in redacted
    assert "QUJDRA" not in redacted
    assert "<redacted-api-key>" in redacted


@pytest.mark.asyncio
async def test_candidate_gate_rejects_optional_multi_protocol_failure(monkeypatch):
    async def passing_minimal(candidate, timeout):
        return {"status": "pass"}

    async def failing_multi(candidate, timeout):
        return {"status": "fail", "errors": ["malformed produced call"]}

    monkeypatch.setattr(probe, "run_minimal", passing_minimal)
    monkeypatch.setattr(probe, "run_multi_content", failing_multi)

    result = await probe.run_candidate(
        Candidate(alias="probe", model="probe/model"),
        timeout=1,
        skip_vision=True,
    )

    assert result["gate_passed"] is False

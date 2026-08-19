import pytest
from types import SimpleNamespace

from models.native_tool_stream import NativeToolCallAssembler, NativeToolStreamError


def _delta(index, *, call_id=None, name=None, arguments=None, call_type=None):
    function = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    value = {"index": index, "function": function}
    if call_id is not None:
        value["id"] = call_id
    if call_type is not None:
        value["type"] = call_type
    return value


def test_assembles_fragmented_parallel_calls_into_standard_wire_shape():
    assembler = NativeToolCallAssembler()
    assembler.add_many([
        _delta(1, call_id="call_b", name="sea", arguments='{"q":'),
        _delta(0, call_id="call_a", name="read_", arguments='{"id":"'),
    ])
    assembler.add_many([
        _delta(0, name="artifact", arguments='a1"}'),
        _delta(1, name="rch", arguments='"x"}'),
    ])

    assert assembler.accept(["tool_calls"]) == [
        {
            "id": "call_a",
            "type": "function",
            "function": {"name": "read_artifact", "arguments": '{"id":"a1"}'},
        },
        {
            "id": "call_b",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        },
    ]


def test_appends_nested_json_fragments_without_content_based_deduplication():
    assembler = NativeToolCallAssembler()
    assembler.add_many([
        _delta(0, call_id="call_123", name="get_", arguments='{"payload":')
    ])
    assembler.add_many([
        _delta(0, name="item", arguments="{"),
        _delta(0, arguments='"id":1}}'),
    ])

    call = assembler.accept(["stop"])[0]
    assert call["id"] == "call_123"
    assert call["function"] == {
        "name": "get_item",
        "arguments": '{"payload":{"id":1}}',
    }
    assert "index" not in call


def test_progress_snapshot_is_sorted_cumulative_and_omits_partial_arguments():
    assembler = NativeToolCallAssembler()
    assembler.add_many([
        _delta(1, call_id="call_b", name="sea", arguments='{"q":'),
        _delta(0, call_id="call_a", name="read_", arguments='{"id":"'),
    ])

    first = assembler.progress_snapshot()
    assert first == [
        {
            "index": 0,
            "call_id": "call_a",
            "name": "read_",
            "arguments_chars": 7,
        },
        {
            "index": 1,
            "call_id": "call_b",
            "name": "sea",
            "arguments_chars": 5,
        },
    ]
    assert all("arguments" not in item for item in first)

    assembler.add_many([
        _delta(0, name="artifact", arguments='a1"}'),
        _delta(1, name="rch", arguments='"x"}'),
    ])
    assert assembler.progress_snapshot() == [
        {
            "index": 0,
            "call_id": "call_a",
            "name": "read_artifact",
            "arguments_chars": 11,
        },
        {
            "index": 1,
            "call_id": "call_b",
            "name": "search",
            "arguments_chars": 9,
        },
    ]


@pytest.mark.parametrize("reasons", [[], ["length"], ["content_filter"]])
def test_rejects_unaccepted_or_missing_terminal_reason(reasons):
    assembler = NativeToolCallAssembler()
    assembler.add_many([_delta(0, call_id="call_1", name="tool", arguments="{}")])

    with pytest.raises(NativeToolStreamError):
        assembler.accept(reasons)


def test_rejects_duplicate_call_ids():
    assembler = NativeToolCallAssembler()
    assembler.add_many([
        _delta(0, call_id="call_same", name="a", arguments="{}"),
        _delta(1, call_id="call_same", name="b", arguments="{}"),
    ])

    with pytest.raises(NativeToolStreamError, match="duplicate"):
        assembler.accept(["tool_calls"])


def test_no_tool_deltas_returns_empty_calls():
    assert NativeToolCallAssembler().accept(["stop"]) == []


async def test_llm_adapter_sends_schemas_and_emits_only_accepted_wire_calls(monkeypatch):
    from models.llm import astream_with_retry

    captured = {}

    def chunk(*, tool_calls=None, finish_reason=None, usage=None, reasoning=None):
        delta = SimpleNamespace(
            content=None,
            reasoning_content=reasoning,
            tool_calls=tool_calls or [],
        )
        return SimpleNamespace(
            usage=usage,
            choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        )

    async def response():
        yield chunk(
            reasoning="checking",
            tool_calls=[SimpleNamespace(
                index=0,
                id="call_7",
                type="function",
                function=SimpleNamespace(name="look", arguments='{"q":'),
            )],
        )
        yield chunk(
            tool_calls=[SimpleNamespace(
                index=0,
                id=None,
                type=None,
                function=SimpleNamespace(name="up", arguments='"x"}'),
            )],
            finish_reason="tool_calls",
            usage=SimpleNamespace(
                prompt_tokens=12,
                completion_tokens=4,
                total_tokens=16,
                prompt_tokens_details=SimpleNamespace(cached_tokens=8),
            ),
        )

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return response()

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )
    monkeypatch.setattr("models.llm._get_client", lambda *_args: client)
    schemas = [{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "lookup",
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    chunks = [
        item async for item in astream_with_retry(
            [{"role": "user", "content": "x"}],
            model="文本模型",
            api_key="test-key",
            user_id="test-user",
            tools=schemas,
        )
    ]

    assert captured["tools"] == schemas
    progress = [item for item in chunks if item["type"] == "tool_call_progress"]
    assert progress == [
        {
            "type": "tool_call_progress",
            "tool_call_progress": [{
                "index": 0,
                "call_id": "call_7",
                "name": "look",
                "arguments_chars": 5,
            }],
        },
        {
            "type": "tool_call_progress",
            "tool_call_progress": [{
                "index": 0,
                "call_id": "call_7",
                "name": "lookup",
                "arguments_chars": 9,
            }],
        },
    ]
    final = chunks[-1]
    assert final["type"] == "final"
    assert final["reasoning_content"] == "checking"
    assert final["token_usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
        "cached_input_tokens": 8,
    }
    assert final["tool_calls"] == [{
        "id": "call_7",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"q":"x"}'},
    }]

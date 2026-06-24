"""
Engine middleware: large tool result auto-persist as artifact.

Covers _maybe_persist_tool_result inside execute_loop.
"""

import asyncio
import math
import uuid
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest

from core.engine import EngineHooks, create_initial_state, execute_loop
from core.events import StreamEventType
from api.services.runtime_store import InMemoryRuntimeStore
from tools.base import ArtifactSpec, BaseTool, ToolPermission, ToolResult


# ============================================================
# Helpers (mirror test_engine_execution.py patterns)
# ============================================================

@dataclass
class _FakeAgentConfig:
    name: str = "lead_agent"
    description: str = "test lead"
    tools: dict = field(default_factory=dict)
    model: str = "openai/fake-model"
    max_tool_rounds: int = 3
    role_prompt: str = "You are a test agent."
    internal: bool = False


class _FixedTool(BaseTool):
    """Tool that always returns a preset ToolResult."""

    def __init__(
        self,
        name: str,
        result: ToolResult,
        max_result_size_chars: float = 50000,
    ):
        super().__init__(
            name=name,
            description=f"Fake {name}",
            permission=ToolPermission.AUTO,
            max_result_size_chars=max_result_size_chars,
        )
        self._result = result

    def get_parameters(self):
        return []

    async def execute(self, **params) -> ToolResult:
        return self._result

    async def __call__(self, **params) -> ToolResult:
        return await self.execute(**params)


class _FakeArtifactManager:
    """Minimal stub: ingest_tool_result returns deterministic id, tracks calls."""

    def __init__(self, *, raise_exc: bool = False, reject: bool = False):
        self.calls: list[tuple[str, str, str]] = []  # (session_id, tool_name, content)
        self.raise_exc = raise_exc
        self.reject = reject
        self._counter = 0

    async def ingest_tool_result(
        self, session_id: str, spec: ArtifactSpec, tool_name: str = None
    ):
        if self.raise_exc:
            raise RuntimeError("simulated DB failure")
        if self.reject:
            return False, "Storage quota exceeded: delete a conversation and retry.", None
        self.calls.append((session_id, tool_name, spec.content))
        self._counter += 1
        return True, "ok", f"tool_{tool_name}_{self._counter:04x}"

    # execute_loop also calls these for inventory; safe no-ops
    def set_session(self, session_id: str) -> None:
        pass

    async def list_artifacts(self, session_id: str, **kwargs):
        return []


def _llm_text(text: str):
    return [
        {"type": "content", "content": text},
        {"type": "usage", "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        {"type": "final", "content": text, "reasoning_content": None,
         "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    ]


def _tool_call_chunks(tool_name: str, **params):
    xml = f"<tool_call>\n<name>{tool_name}</name>\n"
    if params:
        xml += "<params>\n"
        for k, v in params.items():
            xml += f"<{k}><![CDATA[{v}]]></{k}>\n"
        xml += "</params>\n"
    xml += "</tool_call>"
    return _llm_text(xml)


def _make_fake_stream_sequence(rounds: list[list[dict]]):
    call_count = {"n": 0}

    async def fake(messages, **kwargs):
        idx = min(call_count["n"], len(rounds) - 1)
        call_count["n"] += 1
        for c in rounds[idx]:
            yield c

    return fake


def _hooks(store: InMemoryRuntimeStore) -> EngineHooks:
    return EngineHooks(
        check_cancelled=store.is_cancelled,
        wait_for_interrupt=store.wait_for_interrupt,
        drain_messages=store.drain_messages,
    )


async def _run_engine(
    *,
    tool: BaseTool,
    artifact_service=None,
    session_id: str = "sess-1",
    tool_size_threshold: float = 50000,
):
    """Run one tool call then a final text response."""
    state = create_initial_state(
        task="hello",
        session_id=session_id,
        message_id=f"msg-{uuid.uuid4().hex}",
        path_events=[],
    )

    rounds = [
        _tool_call_chunks(tool.name),
        _llm_text("done"),
    ]
    fake_stream = _make_fake_stream_sequence(rounds)

    emitted = []

    async def capture(event_dict):
        emitted.append(event_dict)

    agents = {"lead_agent": _FakeAgentConfig(tools={tool.name: "auto"})}
    store = InMemoryRuntimeStore()

    with patch("models.llm.astream_with_retry", fake_stream), \
         patch("core.engine.config") as mock_config:
        from config import config as real_config
        for attr in dir(real_config):
            if attr.isupper():
                setattr(mock_config, attr, getattr(real_config, attr))
        mock_config.PERMISSION_TIMEOUT = 1
        mock_config.TOOL_PERSIST_PREVIEW_LENGTH = 100  # smaller for test readability

        await execute_loop(
            state=state,
            agents=agents,
            tools={tool.name: tool},
            hooks=_hooks(store),
            artifact_service=artifact_service,
            emit=capture,
        )
    return state, emitted


def _find_tool_complete(emitted: list[dict], tool_name: str) -> dict:
    for e in emitted:
        if e.get("type") == StreamEventType.TOOL_COMPLETE.value and e.get("data", {}).get("tool") == tool_name:
            return e
    raise AssertionError(f"No TOOL_COMPLETE for {tool_name} in emitted events")


# ============================================================
# Tests
# ============================================================

class TestPersistMiddleware:

    async def test_small_result_not_persisted(self):
        """size <= max_result_size_chars → 不落盘，data 原样回填。"""
        tool = _FixedTool("small_tool", ToolResult(success=True, data="short output"))
        manager = _FakeArtifactManager()
        _, emitted = await _run_engine(tool=tool, artifact_service=manager)
        assert manager.calls == []  # 没调用持久化
        complete = _find_tool_complete(emitted, "small_tool")
        assert complete["data"]["result_data"] == "short output"
        assert (complete["data"].get("metadata") or {}).get("persisted_artifact_id") is None

    async def test_large_result_persisted(self):
        """size > max_result_size_chars → 落盘 + 预览 + metadata 标记 artifact_id。"""
        big = "X" * 100_000
        tool = _FixedTool("fetch_tool", ToolResult(success=True, data=big), max_result_size_chars=1000)
        manager = _FakeArtifactManager()
        _, emitted = await _run_engine(tool=tool, artifact_service=manager)

        # Manager 被调用一次
        assert len(manager.calls) == 1
        sid, tname, content = manager.calls[0]
        assert sid == "sess-1"
        assert tname == "fetch_tool"
        assert content == big

        # TOOL_COMPLETE 事件回填的是 envelope 预览
        complete = _find_tool_complete(emitted, "fetch_tool")
        result_data = complete["data"]["result_data"]
        assert "<artifact_slice" in result_data
        assert 'source="tool"' in result_data
        assert 'truncated_by="preview"' in result_data
        assert 'has_more="true"' in result_data
        # body 不应是全文
        assert big not in result_data

        # metadata 标记
        meta = complete["data"]["metadata"]
        assert meta["persisted_artifact_id"].startswith("tool_fetch_tool_")
        assert meta["original_size_chars"] == 100_000

    async def test_max_result_size_chars_inf_bypasses(self):
        """math.inf → 永不落盘，即使输出很长。"""
        big = "X" * 100_000
        tool = _FixedTool("read_like", ToolResult(success=True, data=big), max_result_size_chars=math.inf)
        manager = _FakeArtifactManager()
        _, emitted = await _run_engine(tool=tool, artifact_service=manager)
        assert manager.calls == []
        complete = _find_tool_complete(emitted, "read_like")
        assert complete["data"]["result_data"] == big

    async def test_failed_tool_not_persisted(self):
        """失败的 tool → 不落盘，error 回填原样。"""
        big = "X" * 100_000
        tool = _FixedTool(
            "broken_tool",
            ToolResult(success=False, data=big, error="something broke"),
            max_result_size_chars=1000,
        )
        manager = _FakeArtifactManager()
        _, emitted = await _run_engine(tool=tool, artifact_service=manager)
        assert manager.calls == []
        complete = _find_tool_complete(emitted, "broken_tool")
        # 失败时 result_data=None（原引擎逻辑），error 仍传出
        assert complete["data"]["error"] == "something broke"

    async def test_no_artifact_service_fail_open(self):
        """artifact_service=None → log warning，不阻断，data 原样。"""
        big = "X" * 100_000
        tool = _FixedTool("tool", ToolResult(success=True, data=big), max_result_size_chars=1000)
        _, emitted = await _run_engine(tool=tool, artifact_service=None)
        complete = _find_tool_complete(emitted, "tool")
        # 没落盘 → 长输出原样回填
        assert complete["data"]["result_data"] == big

    async def test_persist_exception_fail_open(self):
        """落盘抛异常 → 捕获 + log，原结果照常回填给模型，不阻断。"""
        big = "X" * 100_000
        tool = _FixedTool("tool", ToolResult(success=True, data=big), max_result_size_chars=1000)
        manager = _FakeArtifactManager(raise_exc=True)
        _, emitted = await _run_engine(tool=tool, artifact_service=manager)
        complete = _find_tool_complete(emitted, "tool")
        # 持久化失败 → fail-open，原文回填
        assert complete["data"]["result_data"] == big

    async def test_declared_artifact_persisted(self):
        """工具声明 result.artifact（具名分支）→ 落盘 + 预览句柄回填，与长度无关。"""
        spec = ArtifactSpec(
            content_type="text/csv", filename="data.csv", content="a,b\n1,2"
        )
        tool = _FixedTool(
            "data_tool", ToolResult(success=True, data="provisional note", artifact=spec)
        )
        manager = _FakeArtifactManager()
        _, emitted = await _run_engine(tool=tool, artifact_service=manager)

        assert len(manager.calls) == 1  # 具名分支不看 max_result_size_chars
        complete = _find_tool_complete(emitted, "data_tool")
        result_data = complete["data"]["result_data"]
        assert "<artifact_slice" in result_data
        assert 'type="text/csv"' in result_data
        assert 'source="tool"' in result_data
        assert complete["data"]["metadata"]["persisted_artifact_id"].startswith("tool_data_tool_")

    async def test_declared_artifact_rejected_surfaces_error(self):
        """具名分支落盘被拒（配额/大小）→ 二进制无法内联，响 success=False 暴露原因。"""
        spec = ArtifactSpec(
            content_type="application/pdf", filename="big.pdf", blob=b"x" * 10
        )
        tool = _FixedTool(
            "dl_tool", ToolResult(success=True, data="note", artifact=spec)
        )
        manager = _FakeArtifactManager(reject=True)
        _, emitted = await _run_engine(tool=tool, artifact_service=manager)
        complete = _find_tool_complete(emitted, "dl_tool")
        # 拒绝原因暴露给模型/用户
        assert "quota" in (complete["data"]["error"] or "").lower()

    async def test_declared_artifact_no_service_fail_open(self):
        """具名分支但 service 缺失 → fail-open，工具自身 data 作兜底。"""
        spec = ArtifactSpec(content_type="text/csv", filename="x.csv", content="a,b")
        tool = _FixedTool(
            "data_tool", ToolResult(success=True, data="fallback note", artifact=spec)
        )
        _, emitted = await _run_engine(tool=tool, artifact_service=None)
        complete = _find_tool_complete(emitted, "data_tool")
        assert complete["data"]["result_data"] == "fallback note"

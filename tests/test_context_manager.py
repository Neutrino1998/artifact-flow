"""
ContextManager unit tests.

Tests system prompt construction, lead vs subagent context, compression,
and artifacts/agents injection.
"""

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from core.context_manager import ContextManager, Context
from core.events import StreamEventType, ExecutionEvent


# ============================================================
# Helpers
# ============================================================


@dataclass
class _FakeAgentConfig:
    name: str = "lead_agent"
    description: str = "Test lead agent"
    tools: dict = field(default_factory=dict)
    model: str = "fake-model"
    max_tool_rounds: int = 3
    role_prompt: str = "You are a helpful assistant."
    internal: bool = False


def _make_state(
    events=None,
    conversation_history=None,
    current_task="hello",
    current_agent="lead_agent",
    session_id="sess-1",
):
    """Build a minimal state dict."""
    return {
        "current_task": current_task,
        "session_id": session_id,
        "message_id": "msg-1",
        "conversation_history": conversation_history or [],
        "completed": False,
        "error": False,
        "current_agent": current_agent,
        "always_allowed_tools": [],
        "events": events or [],
        "response": "",
    }


def _make_event(event_type, agent_name="lead_agent", data=None):
    return ExecutionEvent(event_type=event_type, agent_name=agent_name, data=data)


# ============================================================
# TestSystemPrompt
# ============================================================


class TestSystemPrompt:

    def test_includes_role_prompt(self):
        agent = _FakeAgentConfig(role_prompt="You are a research assistant.")
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(state=state, agent_config=agent, agents={}, tools={})
        system_msg = ctx.messages[0]
        assert system_msg["role"] == "system"
        assert "research assistant" in system_msg["content"]

    def test_includes_system_time(self):
        agent = _FakeAgentConfig()
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(state=state, agent_config=agent, agents={}, tools={})
        system_content = ctx.messages[0]["content"]
        assert "system_time" in system_content

    def test_with_tools_includes_tool_instruction(self):
        from tools.base import BaseTool, ToolPermission, ToolResult, ToolParameter

        class FakeTool(BaseTool):
            def __init__(self):
                super().__init__(name="web_search", description="Search the web", permission=ToolPermission.AUTO)
            def get_parameters(self):
                return [ToolParameter(name="query", type="string", description="Search query")]
            async def execute(self, **p):
                return ToolResult(success=True)

        agent = _FakeAgentConfig(tools={"web_search": "auto"})
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(
            state=state,
            agent_config=agent,
            agents={},
            tools={"web_search": FakeTool()},
        )
        system_content = ctx.messages[0]["content"]
        assert "web_search" in system_content

    def test_no_tools_no_tool_instruction(self):
        agent = _FakeAgentConfig(tools={})
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(state=state, agent_config=agent, agents={}, tools={})
        system_content = ctx.messages[0]["content"]
        # Should not contain tool_call instruction
        assert "tool_call" not in system_content.lower() or "tool" not in system_content.split("<system_time>")[0].lower()


# ============================================================
# TestLeadVsSubagent
# ============================================================


class TestLeadVsSubagent:

    def test_lead_gets_conversation_history(self):
        agent = _FakeAgentConfig()
        history = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "prev answer"},
        ]
        state = _make_state(
            conversation_history=history,
            events=[
                _make_event(StreamEventType.USER_INPUT.value, data={"content": "current"}),
            ],
        )

        ctx = ContextManager.build(state=state, agent_config=agent, agents={}, tools={})
        contents = [m["content"] for m in ctx.messages]
        all_content = " ".join(contents)
        assert "prev question" in all_content
        assert "prev answer" in all_content

    def test_lead_gets_tool_interactions(self):
        agent = _FakeAgentConfig()
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "query"}),
            _make_event(StreamEventType.LLM_COMPLETE.value, data={"content": "I'll search for that"}),
            _make_event(StreamEventType.TOOL_COMPLETE.value, data={
                "tool": "web_search", "success": True, "result_data": "found it",
            }),
        ])

        ctx = ContextManager.build(state=state, agent_config=agent, agents={}, tools={})
        contents = [m["content"] for m in ctx.messages]
        all_content = " ".join(contents)
        assert "query" in all_content
        assert "search" in all_content.lower()

    def test_subagent_only_gets_own_events(self):
        sub_config = _FakeAgentConfig(name="search_agent")
        state = _make_state(
            current_agent="search_agent",
            events=[
                # Lead events — should be excluded
                _make_event(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "user task"}),
                _make_event(StreamEventType.LLM_COMPLETE.value, "lead_agent", {"content": "delegating"}),
                # Subagent events
                _make_event(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "find X"}),
                _make_event(StreamEventType.LLM_COMPLETE.value, "search_agent", {"content": "searching..."}),
                _make_event(StreamEventType.TOOL_COMPLETE.value, "search_agent", {
                    "tool": "web_search", "success": True, "result_data": "found X",
                }),
            ],
        )

        ctx = ContextManager.build(state=state, agent_config=sub_config, agents={}, tools={})
        contents = [m["content"] for m in ctx.messages]
        all_content = " ".join(contents)
        assert "find X" in all_content
        assert "user task" not in all_content

    def test_subagent_does_not_get_history(self):
        sub_config = _FakeAgentConfig(name="search_agent")
        history = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]
        state = _make_state(
            current_agent="search_agent",
            conversation_history=history,
            events=[
                _make_event(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "do task"}),
            ],
        )

        ctx = ContextManager.build(state=state, agent_config=sub_config, agents={}, tools={})
        contents = [m["content"] for m in ctx.messages]
        all_content = " ".join(contents)
        assert "old question" not in all_content

    def test_subagent_instruction_as_user_message(self):
        sub_config = _FakeAgentConfig(name="search_agent")
        state = _make_state(
            current_agent="search_agent",
            events=[
                _make_event(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "find info"}),
            ],
        )

        ctx = ContextManager.build(state=state, agent_config=sub_config, agents={}, tools={})
        # Instruction should appear as a user message
        user_msgs = [m for m in ctx.messages if m["role"] == "user"]
        assert any("find info" in m["content"] for m in user_msgs)


# ============================================================
# TestCompression
# ============================================================


class TestTruncation:

    def test_within_budget_returns_as_is(self):
        messages = [
            {"role": "user", "content": "short"},
            {"role": "assistant", "content": "reply"},
        ]
        result = ContextManager.truncate_messages(messages, budget=10000)
        assert len(result) == 2

    def test_over_budget_drops_oldest(self):
        messages = [
            {"role": "user", "content": "A" * 1000},
            {"role": "assistant", "content": "B" * 1000},
            {"role": "user", "content": "C" * 100},
            {"role": "assistant", "content": "D" * 100},
        ]
        result = ContextManager.truncate_messages(messages, budget=500, preserve_recent=2)
        # Last 2 preserved, first 2 dropped, truncation marker added
        assert result[-1]["content"] == "D" * 100
        assert result[-2]["content"] == "C" * 100
        assert "truncated" in result[0]["content"]
        assert result[0]["role"] == "user"

    def test_preserve_recent_is_hard_limit(self):
        messages = [
            {"role": "user", "content": "X" * 10000},
            {"role": "assistant", "content": "Y" * 10000},
        ]
        result = ContextManager.truncate_messages(messages, budget=500, preserve_recent=4)
        # Only 2 messages, preserve_recent=4 prevents any dropping
        assert len(result) == 2
        assert result[0]["content"] == "X" * 10000

    def test_empty_returns_empty(self):
        result = ContextManager.truncate_messages([], budget=100)
        assert result == []

    def test_build_truncates_history_before_tools(self):
        """Over budget: history gets truncated first, tool interactions preserved."""
        agent = _FakeAgentConfig()
        history = [
            {"role": "user", "content": f"msg {i}" * 500}
            for i in range(20)
        ]
        state = _make_state(
            conversation_history=history,
            events=[
                _make_event(StreamEventType.USER_INPUT.value, data={"content": "current"}),
            ],
        )

        with patch("core.context_manager.config.CONTEXT_MAX_CHARS", 5000):
            ctx = ContextManager.build(
                state=state, agent_config=agent, agents={}, tools={},
            )
        assert len(ctx.messages) >= 2  # system + at least some content
        # "current" (tool interaction) should always be present
        all_content = " ".join(m["content"] for m in ctx.messages)
        assert "current" in all_content


# ============================================================
# TestArtifactsAndAgents
# ============================================================


class TestArtifactsAndAgents:

    def test_task_plan_in_system_prompt(self):
        agent = _FakeAgentConfig(tools={"create_artifact": "auto"})
        artifacts = [
            {"id": "task_plan", "title": "Plan", "version": 1, "content_type": "text/markdown",
             "content": "Step 1: Do X", "updated_at": "2024-01-01", "source": "agent"},
        ]
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(
            state=state, agent_config=agent, agents={}, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = ctx.messages[0]["content"]
        assert "<content>\nStep 1: Do X\n</content>" in system_content
        # id as child element, meta as attributes
        assert '<id>task_plan</id>' in system_content
        assert 'version="1"' in system_content
        assert 'type="text/markdown"' in system_content

    def test_task_plan_full_in_dedicated_section_preview_in_inventory(self):
        """<team_task_plan> wraps full content in <content>; inventory uses <content_preview>."""
        agent = _FakeAgentConfig(tools={"create_artifact": "auto", "read_artifact": "auto"})
        long_content = "A" * 300
        artifacts = [
            {"id": "task_plan", "title": "Plan", "version": 1, "content_type": "text/markdown",
             "content": long_content, "updated_at": "2024-01-01", "source": "agent"},
            {"id": "doc1", "title": "Document", "version": 1, "content_type": "text/plain",
             "content": "Short", "updated_at": "2024-01-01", "source": "agent"},
        ]
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(
            state=state, agent_config=agent, agents={}, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = ctx.messages[0]["content"]
        # <team_task_plan> has full content wrapped in <content>
        assert f"<content>\n{long_content}\n</content>" in system_content
        # inventory uses <content_preview> for truncated, <content> for short
        inv_start = system_content.index("<artifacts_inventory>")
        inv_end = system_content.index("</artifacts_inventory>")
        inventory_section = system_content[inv_start:inv_end]
        assert '<id>task_plan</id>' in inventory_section
        assert '<content_preview length="200">' in inventory_section
        assert long_content not in inventory_section
        # short artifact uses <content> not <content_preview>
        assert "<content>Short</content>" in inventory_section
        assert "2 artifact(s)" in system_content

    def test_artifact_tools_show_inventory(self):
        agent = _FakeAgentConfig(tools={"read_artifact": "auto"})
        artifacts = [
            {"id": "doc1", "title": "Document", "version": 2, "content_type": "text/plain",
             "content": "Some content", "updated_at": "2024-01-01", "source": "agent"},
        ]
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(
            state=state, agent_config=agent, agents={}, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = ctx.messages[0]["content"]
        assert "artifacts_inventory" in system_content
        assert "Document" in system_content

    def test_no_artifact_tools_no_inventory(self):
        agent = _FakeAgentConfig(tools={"web_search": "auto"})
        artifacts = [
            {"id": "doc1", "title": "Document", "version": 1, "content_type": "text/plain",
             "content": "content", "updated_at": "2024-01-01", "source": "agent"},
        ]
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(
            state=state, agent_config=agent, agents={}, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = ctx.messages[0]["content"]
        assert "artifacts_inventory" not in system_content

    def test_call_subagent_shows_available_agents(self):
        lead = _FakeAgentConfig(tools={"call_subagent": "auto"})
        sub = _FakeAgentConfig(name="search_agent", description="Searches the web")

        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(
            state=state,
            agent_config=lead,
            agents={"lead_agent": lead, "search_agent": sub},
            tools={},
        )
        system_content = ctx.messages[0]["content"]
        assert "available_subagents" in system_content
        assert "search_agent" in system_content

    def test_internal_agent_excluded(self):
        lead = _FakeAgentConfig(tools={"call_subagent": "auto"})
        internal = _FakeAgentConfig(name="compact_agent", description="Compacts", internal=True)

        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        ctx = ContextManager.build(
            state=state,
            agent_config=lead,
            agents={"lead_agent": lead, "compact_agent": internal},
            tools={},
        )
        system_content = ctx.messages[0]["content"]
        assert "compact_agent" not in system_content

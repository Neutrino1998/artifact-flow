"""
ContextManager unit tests.

Tests system prompt construction, lead vs subagent context, truncation,
and artifacts/agents injection.
"""

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from core.context_manager import ContextManager
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
    current_task="hello",
    current_agent="lead_agent",
    session_id="sess-1",
):
    """Build a minimal state dict."""
    return {
        "current_task": current_task,
        "session_id": session_id,
        "message_id": "msg-1",
        "completed": False,
        "error": False,
        "current_agent": current_agent,
        "always_allowed_tools": [],
        "events": events or [],
        "response": "",
    }


def _make_event(event_type, agent_name="lead_agent", data=None, is_historical=False):
    return ExecutionEvent(
        event_type=event_type,
        agent_name=agent_name,
        data=data,
        is_historical=is_historical,
    )


def _build(agent, agents=None, **kwargs):
    """Helper: call ContextManager.build with agent_name + agents dict."""
    if agents is None:
        agents = {agent.name: agent}
    elif agent.name not in agents:
        agents[agent.name] = agent
    return ContextManager.build(
        agent_name=agent.name,
        agents=agents,
        **kwargs,
    )


def _ai_msg(content, input_tokens=1000, output_tokens=200):
    """Build an assistant message with _meta for token-based truncation tests."""
    return {
        "role": "assistant",
        "content": content,
        "_meta": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# ============================================================
# TestSystemPrompt
# ============================================================


class TestSystemPrompt:

    def test_includes_role_prompt(self):
        agent = _FakeAgentConfig(role_prompt="You are a research assistant.")
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        messages = _build(agent, state=state, tools={})
        system_msg = messages[0]
        assert system_msg["role"] == "system"
        assert "research assistant" in system_msg["content"]

    def test_includes_system_time(self):
        agent = _FakeAgentConfig()
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        messages = _build(agent, state=state, tools={})
        system_content = messages[0]["content"]
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

        messages = _build(
            agent,
            state=state,
            tools={"web_search": FakeTool()},
        )
        system_content = messages[0]["content"]
        assert "web_search" in system_content

    def test_no_tools_no_tool_instruction(self):
        agent = _FakeAgentConfig(tools={})
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        messages = _build(agent, state=state, tools={})
        system_content = messages[0]["content"]
        # Should not contain tool_call instruction
        assert "tool_call" not in system_content.lower() or "tool" not in system_content.split("<system_time>")[0].lower()


# ============================================================
# TestLeadVsSubagent
# ============================================================


class TestLeadVsSubagent:

    def test_lead_gets_historical_events(self):
        """Historical events (is_historical=True) are included in the LLM context."""
        agent = _FakeAgentConfig()
        state = _make_state(events=[
            # prior-turn events loaded from DB
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "prev question"}, is_historical=True),
            _make_event(StreamEventType.LLM_COMPLETE.value, data={
                "content": "prev answer",
                "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }, is_historical=True),
            # current turn
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "current"}),
        ])

        messages = _build(agent, state=state, tools={})
        all_content = " ".join(m["content"] for m in messages)
        assert "prev question" in all_content
        assert "prev answer" in all_content
        assert "current" in all_content

    def test_lead_gets_tool_interactions(self):
        agent = _FakeAgentConfig()
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "query"}),
            _make_event(StreamEventType.LLM_COMPLETE.value, data={
                "content": "I'll search for that",
                "token_usage": {"input_tokens": 100, "output_tokens": 20},
            }),
            _make_event(StreamEventType.TOOL_COMPLETE.value, data={
                "tool": "web_search", "success": True, "result_data": "found it",
            }),
        ])

        messages = _build(agent, state=state, tools={})
        contents = [m["content"] for m in messages]
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
                _make_event(StreamEventType.LLM_COMPLETE.value, "lead_agent", {
                    "content": "delegating",
                    "token_usage": {"input_tokens": 100, "output_tokens": 20},
                }),
                # Subagent events
                _make_event(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "find X"}),
                _make_event(StreamEventType.LLM_COMPLETE.value, "search_agent", {
                    "content": "searching...",
                    "token_usage": {"input_tokens": 100, "output_tokens": 20},
                }),
                _make_event(StreamEventType.TOOL_COMPLETE.value, "search_agent", {
                    "tool": "web_search", "success": True, "result_data": "found X",
                }),
            ],
        )

        messages = _build(sub_config, state=state, tools={})
        contents = [m["content"] for m in messages]
        all_content = " ".join(contents)
        assert "find X" in all_content
        assert "user task" not in all_content

    def test_subagent_filters_out_lead_historical_events(self):
        """Historical lead events are filtered out from subagent's context (agent_name filter)."""
        sub_config = _FakeAgentConfig(name="search_agent")
        state = _make_state(
            current_agent="search_agent",
            events=[
                # historical lead events from prior turns — should NOT appear in sub context
                _make_event(StreamEventType.USER_INPUT.value, "lead_agent",
                            {"content": "old question"}, is_historical=True),
                _make_event(StreamEventType.LLM_COMPLETE.value, "lead_agent", {
                    "content": "old answer",
                    "token_usage": {"input_tokens": 100, "output_tokens": 20},
                }, is_historical=True),
                # current subagent session
                _make_event(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent",
                            {"instruction": "do task"}),
            ],
        )

        messages = _build(sub_config, state=state, tools={})
        all_content = " ".join(m["content"] for m in messages)
        assert "old question" not in all_content
        assert "old answer" not in all_content
        assert "do task" in all_content

    def test_subagent_instruction_as_user_message(self):
        sub_config = _FakeAgentConfig(name="search_agent")
        state = _make_state(
            current_agent="search_agent",
            events=[
                _make_event(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "find info"}),
            ],
        )

        messages = _build(sub_config, state=state, tools={})
        # Instruction should appear as a user message
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("find info" in m["content"] for m in user_msgs)


# ContextManager.truncate_messages was removed — token-budget truncation is no
# longer a main-path concern. Compaction handles context overflow in-engine via
# CompactionRunner (see tests/test_compaction_runner.py); there is no separate
# fallback truncation code path.


# ============================================================
# TestStripMeta
# ============================================================


class TestStripMeta:

    def test_strips_meta_from_messages(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "_meta": {"input_tokens": 100, "output_tokens": 20}},
        ]
        result = ContextManager._strip_meta(messages)
        assert len(result) == 2
        assert "_meta" not in result[1]
        assert result[1]["content"] == "hello"

    def test_no_meta_passes_through(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = ContextManager._strip_meta(messages)
        assert result == messages

    def test_build_output_has_no_meta(self):
        """Messages returned by build() should not contain _meta."""
        agent = _FakeAgentConfig()
        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "question"}, is_historical=True),
            _make_event(StreamEventType.LLM_COMPLETE.value, data={
                "content": "answer",
                "token_usage": {"input_tokens": 100, "output_tokens": 50},
            }, is_historical=True),
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "current"}),
        ])

        messages = _build(agent, state=state, tools={})
        for msg in messages:
            assert "_meta" not in msg


# _find_last_ai_and_trailing and _build_tool_interactions were removed along
# with truncate_messages — event→message conversion and history scanning now
# live in core/event_history.py and are covered by tests/test_event_history.py.


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

        messages = _build(
            agent, state=state, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = messages[0]["content"]
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

        messages = _build(
            agent, state=state, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = messages[0]["content"]
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

        messages = _build(
            agent, state=state, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = messages[0]["content"]
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

        messages = _build(
            agent, state=state, tools={},
            artifacts_inventory=artifacts,
        )
        system_content = messages[0]["content"]
        assert "artifacts_inventory" not in system_content

    def test_call_subagent_shows_available_agents(self):
        lead = _FakeAgentConfig(tools={"call_subagent": "auto"})
        sub = _FakeAgentConfig(name="search_agent", description="Searches the web")

        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        messages = _build(
            lead,
            agents={"lead_agent": lead, "search_agent": sub},
            state=state,
            tools={},
        )
        system_content = messages[0]["content"]
        assert "available_subagents" in system_content
        assert "search_agent" in system_content

    def test_internal_agent_excluded(self):
        lead = _FakeAgentConfig(tools={"call_subagent": "auto"})
        internal = _FakeAgentConfig(name="compact_agent", description="Compacts", internal=True)

        state = _make_state(events=[
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
        ])

        messages = _build(
            lead,
            agents={"lead_agent": lead, "compact_agent": internal},
            state=state,
            tools={},
        )
        system_content = messages[0]["content"]
        assert "compact_agent" not in system_content

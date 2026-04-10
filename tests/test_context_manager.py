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

        messages = _build(agent, state=state, tools={})
        contents = [m["content"] for m in messages]
        all_content = " ".join(contents)
        assert "prev question" in all_content
        assert "prev answer" in all_content

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

        messages = _build(sub_config, state=state, tools={})
        contents = [m["content"] for m in messages]
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

        messages = _build(sub_config, state=state, tools={})
        # Instruction should appear as a user message
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("find info" in m["content"] for m in user_msgs)


# ============================================================
# TestTruncation
# ============================================================


class TestTruncation:

    def test_within_budget_returns_as_is(self):
        messages = [
            {"role": "user", "content": "short"},
            _ai_msg("reply", input_tokens=100, output_tokens=50),
        ]
        result = ContextManager.truncate_messages(messages, budget=10000)
        assert len(result) == 2

    def test_over_budget_drops_at_ai_boundary(self):
        messages = [
            {"role": "user", "content": "Q1"},
            _ai_msg("A1", input_tokens=5000, output_tokens=1000),
            {"role": "user", "content": "Q2"},
            _ai_msg("A2", input_tokens=8000, output_tokens=2000),
            {"role": "user", "content": "Q3"},
            _ai_msg("A3", input_tokens=12000, output_tokens=3000),
        ]
        # total from last ai = 12000 + 3000 = 15000, budget = 10000
        # First ai savings = 5000 + 1000 = 6000; 15000 - 6000 = 9000 <= 10000 → cut at index 2
        result = ContextManager.truncate_messages(messages, budget=10000, preserve_ai_msgs=1)
        assert "truncated" in result[0]["content"]
        assert result[1]["content"] == "Q2"
        assert result[2]["content"] == "A2"

    def test_preserve_ai_msgs_is_hard_limit(self):
        messages = [
            {"role": "user", "content": "Q1"},
            _ai_msg("A1", input_tokens=50000, output_tokens=10000),
        ]
        # Over budget but only 1 ai msg and preserve_ai_msgs=4
        result = ContextManager.truncate_messages(messages, budget=100, preserve_ai_msgs=4)
        assert len(result) == 2
        assert result[0]["content"] == "Q1"

    def test_empty_returns_empty(self):
        result = ContextManager.truncate_messages([], budget=100)
        assert result == []

    def test_build_truncates_when_over_token_budget(self):
        """Over token budget: messages get truncated."""
        agent = _FakeAgentConfig()
        history = [
            {"role": "user", "content": "old question"},
            _ai_msg("old answer", input_tokens=50000, output_tokens=10000),
            {"role": "user", "content": "newer question"},
            _ai_msg("newer answer", input_tokens=70000, output_tokens=15000),
        ]
        state = _make_state(
            conversation_history=history,
            events=[
                _make_event(StreamEventType.USER_INPUT.value, data={"content": "current"}),
            ],
        )

        with patch("core.context_manager.config.CONTEXT_MAX_TOKENS", 50000):
            messages = _build(agent, state=state, tools={})
        # "current" (tool interaction) should always be present
        all_content = " ".join(m["content"] for m in messages)
        assert "current" in all_content

    def test_no_meta_no_model_not_truncated(self):
        """Without _meta and no model fallback, total=0 → no truncation."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},  # no _meta
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},  # no _meta
        ]
        result = ContextManager.truncate_messages(messages, budget=100)
        assert len(result) == 4

    def test_savings_are_independent_not_cumulative(self):
        """Each AI boundary's savings is independent (cumulative from start).

        Regression: old code had `total -= savings` which double-counted.
        Example: total=20000, A1=5500, A2=11000, A3=16500, budget=5000.
        Correct: total-A2=9000>5000, continue to A3 where total-16500=3500<=5000.
        Buggy (cumulative subtract): after A1 total becomes 14500, then
        14500-11000=3500<=5000, stops at A2 leaving real 20000-11000=9000>5000.
        """
        messages = [
            {"role": "user", "content": "Q1"},
            _ai_msg("A1", input_tokens=5000, output_tokens=500),
            {"role": "user", "content": "Q2"},
            _ai_msg("A2", input_tokens=10000, output_tokens=1000),
            {"role": "user", "content": "Q3"},
            _ai_msg("A3", input_tokens=15000, output_tokens=1500),
            {"role": "user", "content": "Q4"},
            _ai_msg("A4", input_tokens=20000, output_tokens=2000),
        ]
        # total from last AI = 20000+2000 = 22000, budget = 5000, preserve=1
        # A1 savings=5500 → 22000-5500=16500 > 5000
        # A2 savings=11000 → 22000-11000=11000 > 5000
        # A3 savings=16500 → 22000-16500=5500 > 5000
        # All cuttable exhausted (3 cuttable, preserve 1) → cut at A3
        result = ContextManager.truncate_messages(messages, budget=5000, preserve_ai_msgs=1)
        assert "truncated" in result[0]["content"]
        # Should have cut through A3 (index 5), keeping Q4 + A4
        assert result[1]["content"] == "Q4"
        assert result[2]["content"] == "A4"

    def test_no_meta_with_model_fallback(self):
        """When _meta is absent but model is provided, use token_counter fallback."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},  # no _meta
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},  # no _meta
        ]
        with patch("litellm.token_counter") as mock_tc:
            # Total estimation: token_counter(messages) returns 200 (over budget=100)
            # Prefix savings for A1: token_counter(messages[:2]) returns 80
            # 200 - 80 = 120 > 100, continue
            # Only 1 cuttable (2 AIs, preserve=1), cut at A1
            mock_tc.side_effect = lambda **kwargs: (
                200 if len(kwargs.get("messages", [])) == 4
                else 80
            )
            result = ContextManager.truncate_messages(
                messages, budget=100, preserve_ai_msgs=1, model="test-model"
            )
        assert "truncated" in result[0]["content"]
        assert result[1]["content"] == "Q2"

    def test_build_fallback_when_no_meta(self):
        """build() should fallback to token_counter for total when no _meta exists."""
        agent = _FakeAgentConfig()
        history = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},  # no _meta
            {"role": "user", "content": "newer question"},
            {"role": "assistant", "content": "newer answer"},  # no _meta
        ]
        state = _make_state(
            conversation_history=history,
            events=[
                _make_event(StreamEventType.USER_INPUT.value, data={"content": "current"}),
            ],
        )

        with patch("core.context_manager.config.CONTEXT_MAX_TOKENS", 50), \
             patch("core.context_manager.config.TRUNCATION_PRESERVE_AI_MSGS", 1), \
             patch("litellm.token_counter") as mock_tc:
            # Simulate: token_counter returns 200 for all messages (over budget=50)
            # Then for truncation prefix: token_counter(messages[:2]) = 80
            mock_tc.side_effect = lambda **kwargs: (
                200 if len(kwargs.get("messages", [])) >= 4
                else 80
            )
            messages = _build(agent, state=state, tools={}, model="test-model")

        # Should have truncated (marker present) and kept "current"
        all_content = " ".join(m["content"] for m in messages)
        assert "truncated" in all_content
        assert "current" in all_content


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
        history = [
            {"role": "user", "content": "question"},
            _ai_msg("answer", input_tokens=100, output_tokens=50),
        ]
        state = _make_state(
            conversation_history=history,
            events=[
                _make_event(StreamEventType.USER_INPUT.value, data={"content": "current"}),
            ],
        )

        messages = _build(agent, state=state, tools={})
        for msg in messages:
            assert "_meta" not in msg


# ============================================================
# TestFindLastAiAndTrailing
# ============================================================


class TestFindLastAiAndTrailing:

    def test_basic(self):
        messages = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A", "_meta": {"input_tokens": 500, "output_tokens": 100}},
            {"role": "user", "content": "follow-up"},
        ]
        meta, trailing = ContextManager._find_last_ai_and_trailing(messages)
        assert meta["input_tokens"] == 500
        assert meta["output_tokens"] == 100
        assert len(trailing) == 1
        assert trailing[0]["content"] == "follow-up"

    def test_no_ai_messages(self):
        messages = [{"role": "user", "content": "only user"}]
        meta, trailing = ContextManager._find_last_ai_and_trailing(messages)
        assert meta["input_tokens"] == 0
        assert len(trailing) == 1

    def test_ai_is_last(self):
        messages = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A", "_meta": {"input_tokens": 200, "output_tokens": 50}},
        ]
        meta, trailing = ContextManager._find_last_ai_and_trailing(messages)
        assert meta["input_tokens"] == 200
        assert len(trailing) == 0


# ============================================================
# TestToolInteractionMeta
# ============================================================


class TestToolInteractionMeta:

    def test_llm_complete_attaches_meta(self):
        """_build_tool_interactions should attach _meta from token_usage."""
        events = [
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
            _make_event(StreamEventType.LLM_COMPLETE.value, data={
                "content": "response",
                "token_usage": {"input_tokens": 500, "output_tokens": 100},
            }),
        ]
        interactions = ContextManager._build_tool_interactions(events, "lead_agent")
        ai_msgs = [m for m in interactions if m["role"] == "assistant"]
        assert len(ai_msgs) == 1
        assert ai_msgs[0]["_meta"]["input_tokens"] == 500
        assert ai_msgs[0]["_meta"]["output_tokens"] == 100

    def test_llm_complete_no_token_usage_no_meta(self):
        """Without token_usage, no _meta should be attached."""
        events = [
            _make_event(StreamEventType.USER_INPUT.value, data={"content": "hi"}),
            _make_event(StreamEventType.LLM_COMPLETE.value, data={"content": "response"}),
        ]
        interactions = ContextManager._build_tool_interactions(events, "lead_agent")
        ai_msgs = [m for m in interactions if m["role"] == "assistant"]
        assert len(ai_msgs) == 1
        assert "_meta" not in ai_msgs[0]


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

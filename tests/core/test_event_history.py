"""
EventHistory unit tests.

Covers:
- agent_name filtering (lead / sub isolation)
- right-to-left boundary scan for compaction_summary
- right-to-left boundary scan for subagent_instruction with fresh_start=True
- event → message conversion for each event type
- meta attachment on llm_complete
"""

from core.event_history import build_event_history
from core.events import ExecutionEvent, StreamEventType


def _ev(event_type, agent_name="lead_agent", data=None, is_historical=False):
    return ExecutionEvent(
        event_type=event_type,
        agent_name=agent_name,
        data=data,
        is_historical=is_historical,
    )


class TestAgentFiltering:

    def test_lead_only_sees_lead_events(self):
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "LEAD_USER_MSG"}),
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "SUB_INSTR"}),
            _ev(StreamEventType.LLM_COMPLETE.value, "search_agent", {"content": "SUB_REPLY"}),
        ]
        msgs = build_event_history(events, "lead_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "LEAD_USER_MSG" in contents
        assert "SUB_INSTR" not in contents
        assert "SUB_REPLY" not in contents

    def test_subagent_only_sees_own_events(self):
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "LEAD_USER_MSG"}),
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent", {"instruction": "find X"}),
            _ev(StreamEventType.LLM_COMPLETE.value, "search_agent", {"content": "found X"}),
        ]
        msgs = build_event_history(events, "search_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "find X" in contents
        assert "found X" in contents
        assert "LEAD_USER_MSG" not in contents


class TestCompactionSummaryBoundary:

    def test_stops_at_compaction_summary(self):
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "old q"}, is_historical=True),
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent",
                {"content": "old a", "token_usage": {"input_tokens": 100, "output_tokens": 20}},
                is_historical=True),
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "lead_agent",
                {"content": "summary of prior", "error": None}, is_historical=True),
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "new q"}),
        ]
        msgs = build_event_history(events, "lead_agent")
        contents = [m["content"] for m in msgs]
        # Summary replaces everything before it
        assert "old q" not in " ".join(contents)
        assert "old a" not in " ".join(contents)
        assert "summary of prior" in " ".join(contents)
        assert "new q" in " ".join(contents)

    def test_uses_most_recent_summary(self):
        """Multiple summaries in stream: only the rightmost one matters."""
        events = [
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "lead_agent",
                {"content": "first summary"}, is_historical=True),
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "mid"}, is_historical=True),
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent",
                {"content": "reply", "token_usage": {"input_tokens": 1, "output_tokens": 1}},
                is_historical=True),
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "lead_agent",
                {"content": "second summary"}, is_historical=True),
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "latest"}),
        ]
        msgs = build_event_history(events, "lead_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "second summary" in contents
        assert "first summary" not in contents
        assert "mid" not in contents
        assert "latest" in contents

    def test_failed_compaction_summary_does_not_form_boundary(self):
        """
        success=False compaction_summary is a paired terminator for compaction_start
        (UI / replay only). EventHistory must skip it: prior events stay visible
        and the failure marker itself does not become a user message. Otherwise
        a failed compaction would silently amputate mid-turn context.
        """
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "old q"}, is_historical=True),
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent",
                {"content": "old a", "token_usage": {"input_tokens": 100, "output_tokens": 20}},
                is_historical=True),
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "lead_agent",
                {"success": False, "content": "", "error": "LLM unreachable"},
                is_historical=True),
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "new q"}),
        ]
        msgs = build_event_history(events, "lead_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "old q" in contents
        assert "old a" in contents
        assert "new q" in contents
        assert "LLM unreachable" not in contents

    def test_failed_summary_falls_through_to_earlier_successful_summary(self):
        """If a successful summary exists earlier, scan should land on it (skipping the failed marker)."""
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "ancient"}, is_historical=True),
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "lead_agent",
                {"success": True, "content": "good summary", "error": None},
                is_historical=True),
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent",
                {"content": "post-summary reply", "token_usage": {"input_tokens": 1, "output_tokens": 1}},
                is_historical=True),
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "lead_agent",
                {"success": False, "content": "", "error": "boom"},
                is_historical=True),
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "new q"}),
        ]
        msgs = build_event_history(events, "lead_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "ancient" not in contents
        assert "good summary" in contents
        assert "post-summary reply" in contents
        assert "new q" in contents

    def test_compaction_summary_is_agent_scoped(self):
        """A sub-agent compaction_summary does NOT affect lead history."""
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "lead-u"}),
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "search_agent",
                {"content": "sub-summary"}),
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent",
                {"content": "lead-a", "token_usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
        msgs = build_event_history(events, "lead_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "lead-u" in contents
        assert "lead-a" in contents
        assert "sub-summary" not in contents


class TestFreshStartBoundary:

    def test_subagent_fresh_start_stops_scan(self):
        """Fresh-start subagent_instruction isolates current call from prior sub sessions."""
        events = [
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent",
                {"instruction": "old-call", "fresh_start": True}),
            _ev(StreamEventType.LLM_COMPLETE.value, "search_agent",
                {"content": "old-reply", "token_usage": {"input_tokens": 1, "output_tokens": 1}}),
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent",
                {"instruction": "new-call", "fresh_start": True}),
        ]
        msgs = build_event_history(events, "search_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "new-call" in contents
        assert "old-call" not in contents
        assert "old-reply" not in contents

    def test_subagent_fresh_start_false_keeps_prior_session(self):
        """fresh_start=False: subagent sees accumulated prior sessions."""
        events = [
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent",
                {"instruction": "session1", "fresh_start": True}),
            _ev(StreamEventType.LLM_COMPLETE.value, "search_agent",
                {"content": "r1", "token_usage": {"input_tokens": 1, "output_tokens": 1}}),
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent",
                {"instruction": "session2", "fresh_start": False}),
        ]
        msgs = build_event_history(events, "search_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "session1" in contents
        assert "r1" in contents
        assert "session2" in contents

    def test_fresh_start_is_subagent_only(self):
        """Lead agent is not affected by fresh_start markers."""
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "u1"}),
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "lead_agent",
                {"instruction": "boundary?", "fresh_start": True}),
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "u2"}),
        ]
        msgs = build_event_history(events, "lead_agent")
        contents = " ".join(m["content"] for m in msgs)
        # Lead agent should see both u1 and u2 — fresh_start only stops the sub scan
        assert "u1" in contents
        assert "u2" in contents

    def test_compaction_summary_wins_over_fresh_start(self):
        """Most recent boundary wins; if compaction_summary is right of fresh_start, it's the boundary."""
        events = [
            _ev(StreamEventType.SUBAGENT_INSTRUCTION.value, "search_agent",
                {"instruction": "OLD_INSTR", "fresh_start": True}),
            _ev(StreamEventType.LLM_COMPLETE.value, "search_agent",
                {"content": "OLD_REPLY", "token_usage": {"input_tokens": 1, "output_tokens": 1}}),
            _ev(StreamEventType.COMPACTION_SUMMARY.value, "search_agent",
                {"content": "sub summary"}),
            _ev(StreamEventType.TOOL_COMPLETE.value, "search_agent",
                {"tool": "web_search", "success": True, "result_data": "x"}),
        ]
        msgs = build_event_history(events, "search_agent")
        contents = " ".join(m["content"] for m in msgs)
        assert "sub summary" in contents
        assert "OLD_INSTR" not in contents
        assert "OLD_REPLY" not in contents


class TestMessageConversion:

    def test_llm_complete_carries_meta(self):
        events = [
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent", {
                "content": "hi",
                "token_usage": {"input_tokens": 123, "output_tokens": 45},
            }),
        ]
        msgs = build_event_history(events, "lead_agent")
        ai = [m for m in msgs if m["role"] == "assistant"]
        assert len(ai) == 1
        assert ai[0]["_meta"] == {"input_tokens": 123, "output_tokens": 45}

    def test_compaction_start_is_ignored(self):
        """compaction_start is persisted but not a history-building event type."""
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "u"}),
            _ev(StreamEventType.COMPACTION_START.value, "lead_agent",
                {"last_input_tokens": 60000, "last_output_tokens": 2000}),
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent",
                {"content": "a", "token_usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
        msgs = build_event_history(events, "lead_agent")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_tool_complete_renders_as_user_with_xml(self):
        events = [
            _ev(StreamEventType.TOOL_COMPLETE.value, "lead_agent", {
                "tool": "web_search", "success": True, "result_data": "found",
            }),
        ]
        msgs = build_event_history(events, "lead_agent")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "<tool_result" in msgs[0]["content"]

    def test_empty_events_returns_empty(self):
        assert build_event_history([], "lead_agent") == []

    def test_no_boundary_returns_all_matching(self):
        """No compaction_summary / fresh_start → whole agent-filtered stream is used."""
        events = [
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "u1"}),
            _ev(StreamEventType.LLM_COMPLETE.value, "lead_agent",
                {"content": "a1", "token_usage": {"input_tokens": 1, "output_tokens": 1}}),
            _ev(StreamEventType.USER_INPUT.value, "lead_agent", {"content": "u2"}),
        ]
        msgs = build_event_history(events, "lead_agent")
        assert len(msgs) == 3
        assert msgs[0]["content"] == "u1"
        assert msgs[1]["content"] == "a1"
        assert msgs[2]["content"] == "u2"


class TestVisionImageBlock:
    """识图 tool_complete 携图片引用 + vision_blocks 缓存 → 块列表 vs 占位文本门控。"""

    _IMG_EVENT = dict(
        tool="read_artifact", success=True, result_data="[image artifact 'shot' v1, image/png]",
        metadata={"image": {"artifact_id": "shot", "version": 1, "content_type": "image/png"}},
    )
    _DATA_URI = "data:image/png;base64,AAAA"

    def _events(self):
        return [_ev(StreamEventType.TOOL_COMPLETE.value, "lead_agent", dict(self._IMG_EVENT))]

    def test_hit_and_vision_capable_expands_to_block_list(self):
        msgs = build_event_history(
            self._events(), "lead_agent",
            vision_blocks={("shot", 1): self._DATA_URI}, vision_capable=True,
        )
        assert len(msgs) == 1
        content = msgs[0]["content"]
        assert isinstance(content, list)
        assert any(b["type"] == "image_url" and b["image_url"]["url"] == self._DATA_URI for b in content)

    def test_hit_but_not_vision_capable_falls_back_to_placeholder(self):
        """文本模型(vision_capable=False)即便缓存命中也只得占位文本 —— 不注入 image_url 块。
        且文案主体是模型(you can't view),不诱导无效重读(重读也永远看不到)。"""
        msgs = build_event_history(
            self._events(), "lead_agent",
            vision_blocks={("shot", 1): self._DATA_URI}, vision_capable=False,
        )
        assert len(msgs) == 1
        content = msgs[0]["content"]
        assert isinstance(content, str)
        assert "you can't view images" in content
        assert "not multimodal" in content  # 指明不能看的原因是模型非多模态
        assert "re-read" not in content  # 文本模型不应被诱导重读
        assert self._DATA_URI not in content

    def test_miss_falls_back_to_placeholder_even_if_vision_capable(self):
        """跨轮(state 已空、缓存未命中)→ 占位文本「需要再看就重读」(条件式,非命令)。"""
        msgs = build_event_history(
            self._events(), "lead_agent", vision_blocks={}, vision_capable=True,
        )
        assert isinstance(msgs[0]["content"], str)
        assert "re-read artifact 'shot' if you need to view it" in msgs[0]["content"]

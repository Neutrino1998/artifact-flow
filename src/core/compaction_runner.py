"""
In-engine compaction — 引擎内同步触发的上下文压缩

设计：
- 在 engine loop 每次 LLM call 后调用 maybe_trigger：检查 last_input+output_tokens
  是否超阈值，若超则立即执行一次 compaction。
- 不再使用异步后台任务、分布式锁、heartbeat 续租等机制（engine 是单协程流）。
- 生成的 compaction_summary 作为 ExecutionEvent **追加到 state["events"] 尾部**：
  EventHistory 从右往左扫描会在此处停下，之前的 events 在 context 构建中被跳过。
  下一轮 load path events 时，这条 compaction_summary 天然出现在历史段的尾部，
  所以下一轮首次 LLM call 的 history 自动就是 [summary, 新 user_input]，不会超窗。
- 压缩只作用于同一个 agent（按 agent_name 过滤），lead 和 subagent 互不干扰。
- Compaction LLM 失败时降级为 "占位 summary"，boundary 语义不变，效果等价硬截断，
  不需要单独的 truncate 兜底路径。
"""

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from config import config
from core.events import ExecutionEvent, StreamEventType
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]

# Prepended to the summary text when persisted. Role parallels queued_message's
# "[The user has injected a message...]" framing: the DB-stored content is the
# LLM-ready form, so EventHistory does no runtime wrapping. The frame tells the
# LLM the user message it's looking at is a memory aid, not a fresh user input.
_SUMMARY_FRAME = (
    "[Prior conversation has been compacted into this summary. "
    "Treat it as your memory of earlier context and continue from here.]"
)


class CompactionRunner:
    """
    引擎内 compaction 执行器。

    生命周期等同 execute_loop —— 每次 stream_execute 调用创建一次，用完即丢。
    """

    def __init__(self, agents: Dict[str, Any], emit: Optional[EmitFn] = None):
        self._agents = agents
        self._emit = emit

    async def maybe_trigger(
        self,
        state: Dict[str, Any],
        agent_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """
        LLM 调用完成后的 compaction 检查入口。

        超阈值则生成 compaction_summary 并**追加到 state["events"] 尾部**。
        非超阈值 / 无 compact_agent 时静默跳过。
        """
        if input_tokens + output_tokens <= config.COMPACTION_TOKEN_THRESHOLD:
            return

        compact_agent = self._agents.get("compact_agent")
        if not compact_agent:
            logger.warning("compact_agent not configured, skipping compaction")
            return

        logger.debug(
            f"[compaction] triggered for {agent_name}: "
            f"threshold={config.COMPACTION_TOKEN_THRESHOLD}, "
            f"last_call input={input_tokens} output={output_tokens} "
            f"(sum={input_tokens + output_tokens}), "
            f"events_in_state={len(state['events'])}"
        )

        # compaction_start 同时入 state["events"]（持久化）+ SSE，便于中途重连的 replay
        # 看到"压缩进行中"指示器，而不是看完最后一个 llm_complete 就等到 summary。
        start_data = {
            "last_input_tokens": input_tokens,
            "last_output_tokens": output_tokens,
        }
        start_event = ExecutionEvent(
            event_type=StreamEventType.COMPACTION_START.value,
            agent_name=agent_name,
            data=start_data,
            is_historical=False,
        )
        state["events"].append(start_event)
        await self._emit_sse(StreamEventType.COMPACTION_START.value, agent_name, start_data)

        # 快照当前 events 作为 compact 输入。注意：必须在 append summary_event 之前快照，
        # 否则新 summary 会被包含进"要被自己压缩"的输入里。
        # compaction_start 也在快照里 —— 它会被 EventHistory 的过滤器自然忽略（不是 history-building
        # 关心的事件类型），所以不影响压缩输入。
        events_to_compact = list(state["events"])

        try:
            content, duration_ms, usage = await self._run_compact_llm(
                events_to_compact, agent_name, compact_agent
            )
            error: Optional[str] = None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Fallback: compaction_summary 照样 append 到尾部，只是内容变占位符。
            # EventHistory 右扫左依旧在此断开，之前的 events 在 context 构建中被跳过，
            # 等价于硬截断。
            logger.exception(f"Compaction LLM failed for {agent_name}: {e}")
            content = f"[compaction failed: {e}. Earlier context was truncated.]"
            duration_ms = 0
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            error = str(e)

        # Prepend the memory-aid frame so the LLM treats this user-role message
        # as a condensed summary rather than a fresh user prompt.
        framed_content = f"{_SUMMARY_FRAME}\n\n{content}"

        summary_event = ExecutionEvent(
            event_type=StreamEventType.COMPACTION_SUMMARY.value,
            agent_name=agent_name,
            data={
                "content": framed_content,
                "token_usage": usage,
                "duration_ms": duration_ms,
                "model": compact_agent.model,
                "error": error,
            },
            is_historical=False,
        )
        state["events"].append(summary_event)

        await self._emit_sse(
            StreamEventType.COMPACTION_SUMMARY.value,
            agent_name,
            summary_event.data,
        )

    # ─────────────────────────────────────────────────────────────
    # 内部实现
    # ─────────────────────────────────────────────────────────────

    async def _run_compact_llm(
        self,
        events_to_compact: List[ExecutionEvent],
        agent_name: str,
        compact_agent: Any,
    ) -> Tuple[str, int, Dict[str, int]]:
        """
        调用 compact_agent LLM，返回 (summary_content, duration_ms, token_usage)。
        """
        from core.event_history import build_event_history
        from models.llm import astream_with_retry, format_messages_for_debug

        # 按 agent_name 过滤 + boundary 扫描，得到用于压缩的历史 messages
        history = build_event_history(events_to_compact, agent_name)
        clean_history = [
            {k: v for k, v in m.items() if k != "_meta"} for m in history
        ]

        if not clean_history:
            # 没有任何可压缩内容（理论上不应进这里，但兜个底）
            return (
                "[no prior content to summarize]",
                0,
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": compact_agent.role_prompt}
        ]
        messages.extend(clean_history)
        messages.append({
            "role": "user",
            "content": (
                "Please provide your detailed summary of the conversation so far now, "
                "following the structure specified in your instructions. Do not call "
                "any tools — respond with plain text only."
            ),
        })

        logger.debug(
            f"[compact_agent] Messages (for {agent_name} compaction, "
            f"{len(events_to_compact)} events):\n{format_messages_for_debug(messages)}"
        )

        start = datetime.now()
        response = ""
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        async def _stream():
            nonlocal response, usage
            async for chunk in astream_with_retry(messages, model=compact_agent.model):
                ct = chunk.get("type")
                if ct == "content":
                    response += chunk["content"]
                elif ct == "usage":
                    tu = chunk.get("token_usage") or {}
                    usage = {
                        "input_tokens": tu.get("prompt_tokens", 0),
                        "output_tokens": tu.get("completion_tokens", 0),
                        "total_tokens": tu.get("total_tokens", 0),
                    }
                elif ct == "final":
                    if not response and chunk.get("content"):
                        response = chunk["content"]
                    tu = chunk.get("token_usage")
                    if tu and not usage["total_tokens"]:
                        usage = {
                            "input_tokens": tu.get("prompt_tokens", 0),
                            "output_tokens": tu.get("completion_tokens", 0),
                            "total_tokens": tu.get("total_tokens", 0),
                        }

        async with asyncio.timeout(config.COMPACTION_TIMEOUT):
            await _stream()

        duration_ms = int((datetime.now() - start).total_seconds() * 1000)

        # The entire response is the summary — compact_agent is instructed to
        # emit the numbered sections directly with no outer wrapper. We do NOT
        # parse or extract: any wrapper regex would be vulnerable to the
        # required `<quote>` verbatim user text containing matching tag
        # literals (e.g. a user saying "how do I write </summary>" would
        # truncate a <summary>...</summary> regex extraction).
        content = response.strip()

        if not content:
            raise RuntimeError("compact_agent produced empty summary")

        # No truncation on compact response — the whole point of this log is
        # reviewing summary quality. DEBUG level keeps it off in prod.
        logger.debug(
            f"[compact_agent] LLM Response (input: {usage['input_tokens']}, "
            f"output: {usage['output_tokens']}):\n{content}"
        )
        logger.info(
            f"[compaction] {agent_name}: compressed {len(events_to_compact)} events "
            f"in {duration_ms}ms (in={usage['input_tokens']}, out={usage['output_tokens']})"
        )
        return content, duration_ms, usage

    async def _emit_sse(self, event_type: str, agent_name: str, data: Any) -> None:
        if not self._emit:
            return
        await self._emit({
            "type": event_type,
            "agent": agent_name,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        })

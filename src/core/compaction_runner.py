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
- Compaction LLM 失败 → append 一个 success=False 的 compaction_summary 配对
  compaction_start，然后 raise。EventHistory 跳过 success=False 的 summary（既不
  作 boundary 也不入 messages），由 engine 调用方接住异常把整个 turn 标 ERROR。
  不引入占位 summary —— 在 turn 中段插入"等价硬截断"会让模型完全失忆刚发生的
  llm_complete / 即将到达的 tool_result，无法续接。
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from config import config
from core.cancellation import CooperativeCancelled, run_cancellable
from core.events import ExecutionEvent, StreamEventType
from utils.logger import get_logger
from utils.time import utc_now

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

    def __init__(
        self,
        agents: Dict[str, Any],
        emit: Optional[EmitFn] = None,
        check_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
    ):
        self._agents = agents
        self._emit = emit
        # 零参 async 谓词（engine 预绑定 message_id）。提供时 compaction LLM 调用
        # 变为可被协作式 cancel 打断（抛 CooperativeCancelled）—— 否则该调用是
        # 长达 COMPACTION_TIMEOUT 的 cancel 盲窗。None = 不轮询（独立测试场景）。
        self._check_cancelled = check_cancelled

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

        state["force_compact"]（用户手动触发）为真且 agent 为 lead 时无视阈值强制压缩一次：
        在此立即消费标志（置 False），故一轮内只压一次、后续 LLM call 不会重复触发。
        """
        forced = bool(state.get("force_compact")) and agent_name == "lead_agent"
        if forced:
            state["force_compact"] = False

        if not forced and input_tokens + output_tokens <= config.COMPACTION_TOKEN_THRESHOLD:
            return

        compact_agent = self._agents.get("compact_agent")
        if not compact_agent:
            logger.warning("compact_agent not configured, skipping compaction")
            return

        # 提到 INFO:compaction 触发条件是关键状态转移,事故诊断必需(对齐
        # "工具完成/状态转移"分级原则;尺寸字段而非大体积内容,可常驻 INFO)。
        logger.info(
            f"[compaction] triggered for {agent_name}: "
            f"trigger={'forced' if forced else 'threshold'}, "
            f"threshold={config.COMPACTION_TOKEN_THRESHOLD}, "
            f"last_call input={input_tokens} output={output_tokens} "
            f"(sum={input_tokens + output_tokens}), "
            f"events_in_state={len(state['events'])}"
        )

        # compaction_start 同时入 state["events"]（持久化）+ SSE，便于中途重连的 replay
        # 看到"压缩进行中"指示器，而不是看完最后一个 llm_complete 就等到 summary。
        # forced 标记手动触发，供前端/replay 区分「用户压缩」与「超阈值自动压缩」。
        start_data = {
            "last_input_tokens": input_tokens,
            "last_output_tokens": output_tokens,
            "forced": forced,
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
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Append a success=False compaction_summary so compaction_start has a
            # paired terminator (event stream stays well-formed for replay / UI),
            # then re-raise. EventHistory ignores success=False summaries entirely
            # — no boundary, no message — so the next LLM call still sees full
            # uncompacted history. The engine catches this exception and marks
            # the turn ERROR; we don't silently continue with a broken context.
            # CooperativeCancelled（用户取消打断 compaction）共用同一配对路径，但
            # 它是预期的用户行为不是故障 —— info 级、无栈，engine 把它路由到
            # CANCELLED 而非 ERROR。
            if isinstance(e, CooperativeCancelled):
                logger.info(f"Compaction for {agent_name} cancelled by user mid-call")
            else:
                logger.exception(f"Compaction LLM failed for {agent_name}: {e}")
            failure_data = {
                "success": False,
                "content": "",
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "duration_ms": 0,
                "model": compact_agent.model,
                "error": str(e),
            }
            state["events"].append(ExecutionEvent(
                event_type=StreamEventType.COMPACTION_SUMMARY.value,
                agent_name=agent_name,
                data=failure_data,
                is_historical=False,
            ))
            await self._emit_sse(
                StreamEventType.COMPACTION_SUMMARY.value,
                agent_name,
                failure_data,
            )
            raise

        # Prepend the memory-aid frame so the LLM treats this user-role message
        # as a condensed summary rather than a fresh user prompt.
        framed_content = f"{_SUMMARY_FRAME}\n\n{content}"

        summary_event = ExecutionEvent(
            event_type=StreamEventType.COMPACTION_SUMMARY.value,
            agent_name=agent_name,
            data={
                "success": True,
                "content": framed_content,
                "token_usage": usage,
                "duration_ms": duration_ms,
                "model": compact_agent.model,
                "error": None,
            },
            is_historical=False,
        )
        state["events"].append(summary_event)

        # 下一轮 gauge 准确性：把 compaction call 的 output_tokens（= summary 大小，
        # 亦即折叠后下一次 call 实际载入的「历史」内容大小）回写为 last_input_tokens,
        # 作为「下一次 lead call 输入大小」的实测代理（纯依赖 usage,不调 tokenizer,
        # 对齐与 maybe_trigger 同源的可移植性约束）。
        # 这条写入只在「compaction 触发在 final response 之后、loop 即将结束」这一窗口
        # 实际生效 —— 其他情况后续 lead call 会以真实 input_tokens 覆盖（engine.py:425）,
        # 本写入被自然丢弃；故无需特判「是不是终态前一次」。
        # 仅对 lead 写入：last_input_tokens 是 lead-only 字段（约束见 engine.py:425 +
        # docs/architecture/engine.md），subagent compaction 不能污染此字段 —— 否则若
        # subagent 压缩后、下次 lead call 覆盖前发生 cancel/timeout/error,持久化会留下
        # subagent summary 的 token 数,导致 composer gauge 显著低估 lead 上下文。
        # gauge 分子 = last_input + last_output（与 compaction 触发口径 input+output 对齐）,
        # 故这里把 output 项一并归零：压缩后上下文只剩 summary（= 下一次 call 的 input,
        # 尚无 output 分量),不归零会让 stale last_output 叠进 gauge,削弱「压缩后回落」。
        if agent_name == "lead_agent":
            metrics = state.get("execution_metrics")
            if metrics is not None:
                metrics["last_input_tokens"] = usage.get("output_tokens", 0)
                metrics["last_output_tokens"] = 0

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

        if logger.debug_mode:
            logger.debug(
                f"[compact_agent] Messages (for {agent_name} compaction, "
                f"{len(events_to_compact)} events):\n{format_messages_for_debug(messages)}"
            )

        start = utc_now()
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

        async def _guarded_stream():
            async with asyncio.timeout(config.COMPACTION_TIMEOUT):
                await _stream()

        if self._check_cancelled is not None:
            # 可打断 await：用户 cancel 不再等满 COMPACTION_TIMEOUT（原本是默认配置
            # 下全系统最长的 cancel 盲窗）。CooperativeCancelled 穿透到 maybe_trigger
            # 配对 success=False 占位 summary，再由 engine 路由 CANCELLED 终态。
            # asyncio.timeout 在子 task 内、只转换自己 deadline 的取消 —— 与外层
            # run_cancellable 的 task.cancel() 不混淆（3.11+ 语义，同 controller.py）。
            await run_cancellable(
                _guarded_stream(), self._check_cancelled, config.CANCEL_CHECK_INTERVAL
            )
        else:
            await _guarded_stream()

        duration_ms = int((utc_now() - start).total_seconds() * 1000)

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
            "timestamp": utc_now().isoformat(),
            "data": data,
        })

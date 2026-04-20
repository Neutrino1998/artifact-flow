"""
In-engine compaction — 引擎内同步触发的上下文压缩

设计：
- 在 engine loop 每次 LLM call 后调用 maybe_trigger：检查 last_input+output_tokens
  是否超阈值，若超则立即执行一次 compaction。
- 不再使用异步后台任务、分布式锁、heartbeat 续租等机制（engine 是单协程流）。
- 生成的 compaction_summary 作为 ExecutionEvent 插入 state["events"] 的 preserve
  边界位置（而非 append 到尾部），这样 EventHistory 从右往左扫描能正确识别边界。
- 压缩只作用于同一个 agent（按 agent_name 过滤），lead 和 subagent 互不干扰。
- Preserve 窗口只在当前轮（is_historical=False）events 中扫描，不跨轮。
- Compaction LLM 失败时降级到 "synthetic summary + 硬截断占位"，不再让 ContextManager
  做主路径的 truncation。
"""

import asyncio
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from config import config
from core.events import ExecutionEvent, StreamEventType
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]


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

        超阈值则生成 compaction_summary 并插入 state["events"] 的 preserve 边界。
        非超阈值 / 无 compact_agent 时静默跳过。
        """
        if input_tokens + output_tokens <= config.COMPACTION_TOKEN_THRESHOLD:
            return

        compact_agent = self._agents.get("compact_agent")
        if not compact_agent:
            logger.warning("compact_agent not configured, skipping compaction")
            return

        await self._emit_sse(StreamEventType.COMPACTION_START.value, agent_name, {
            "last_input_tokens": input_tokens,
            "last_output_tokens": output_tokens,
        })

        boundary_idx = self._find_preserve_boundary(state, agent_name)
        events_to_compact = state["events"][:boundary_idx]

        try:
            content, duration_ms, usage = await self._run_compact_llm(
                events_to_compact, agent_name, compact_agent
            )
            error: Optional[str] = None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Fallback: compaction_summary 仍然插入到 preserve 边界（boundary_idx 已计算好），
            # 只是内容变成占位符。EventHistory 扫描时依旧会在此处断开，boundary 前的原始
            # events 在 context 构建中被跳过 —— 等价于 "硬截断" 但不需要单独的截断逻辑。
            logger.exception(f"Compaction LLM failed for {agent_name}: {e}")
            content = (
                f"[compaction failed: {e}. Earlier conversation context was "
                f"dropped to fit the context budget. Continue from the preserved "
                f"recent messages.]"
            )
            duration_ms = 0
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            error = str(e)

        summary_event = ExecutionEvent(
            event_type=StreamEventType.COMPACTION_SUMMARY.value,
            agent_name=agent_name,
            data={
                "content": content,
                "token_usage": usage,
                "duration_ms": duration_ms,
                "error": error,
            },
            is_historical=False,
        )
        state["events"].insert(boundary_idx, summary_event)

        await self._emit_sse(
            StreamEventType.COMPACTION_SUMMARY.value,
            agent_name,
            summary_event.data,
        )

    # ─────────────────────────────────────────────────────────────
    # 内部实现
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _find_preserve_boundary(state: Dict[str, Any], agent_name: str) -> int:
        """
        在 state["events"] 里从右往左扫描，找 preserve 窗口的起始索引（compaction_summary
        将被插在这个索引处，使其出现在 preserve 窗口之前）。

        语义：
        - 保留最近 K 个 "该 agent 自己的 llm_complete" 及其之间/之后的所有 event
        - 遇到 is_historical=True 即停止（preserve 不跨轮）
        - 若当前轮该 agent 的 llm_complete 少于 K，按实际数量保留
        """
        K = config.COMPACTION_PRESERVE_LLM_COMPLETES
        events = state["events"]
        count = 0
        boundary = len(events)  # 默认：preserve 窗口为空（全部可压）

        for i in range(len(events) - 1, -1, -1):
            ev = events[i]
            if ev.is_historical:
                break
            if (
                ev.agent_name == agent_name
                and ev.event_type == StreamEventType.LLM_COMPLETE.value
            ):
                count += 1
                boundary = i
                if count >= K:
                    break
        return boundary

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
        from models.llm import astream_with_retry

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
                "wrapped in a single <summary> tag following the structure specified in "
                "your instructions. Do not call any tools — respond with plain text only."
            ),
        })

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

        # 解析 <summary> 标签
        match = re.search(r"<summary>([\s\S]*?)</summary>", response)
        content = match.group(1).strip() if match else response.strip()

        if not content:
            raise RuntimeError("compact_agent produced empty summary")

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

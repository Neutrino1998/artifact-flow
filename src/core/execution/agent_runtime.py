"""Execution boundary around the Pi-style agent loop.

The runtime owns loop execution, its deadline, and factual stop-reason
normalization.  It deliberately does not know about Conversation persistence,
Redis ownership, streams, repositories, or final terminal events.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from config import config
from utils.logger import get_logger, get_request_id

logger = get_logger("ArtifactFlow")


EventSink = Callable[[Dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class RuntimeHooks:
    """Runtime callbacks supplied by either Web or embedded callers."""

    check_cancelled: Callable[[str], Awaitable[bool]]
    wait_for_interrupt: Callable[
        [str, Dict[str, Any], float],
        Awaitable[Optional[Dict[str, Any]]],
    ]
    drain_messages: Callable[[str], Awaitable[List[str]]]


async def execute_loop(**kwargs):
    """Lazy seam keeps importing AgentRuntime independent from engine adapters."""
    from core.execution.engine import execute_loop as run_engine_loop

    return await run_engine_loop(**kwargs)


class StopReason(str, Enum):
    """Why the engine loop stopped; this is not a persisted terminal type."""

    COMPLETE = "complete"
    TIMEOUT = "timeout"
    COOPERATIVE_CANCEL = "cooperative_cancel"
    EXTERNAL_CANCEL = "external_cancel"
    ERROR = "error"


def get_stop_reason(state: Mapping[str, Any]) -> Optional[StopReason]:
    """Return the invocation's control-plane terminal, if one was chosen."""
    value = state.get("stop_reason")
    if value is None or isinstance(value, StopReason):
        return value
    return StopReason(value)


def stop_execution(
    state: Dict[str, Any],
    reason: StopReason,
    *,
    replace: bool = False,
) -> StopReason:
    """Transition one invocation from running to a terminal reason.

    Engine paths use the default first-terminal-wins behavior.  Runtime boundary
    failures may replace the reason when they are the later authoritative fact
    (most notably the outer execution deadline).
    """
    current = get_stop_reason(state)
    if current is not None and not replace:
        return current
    state["stop_reason"] = reason
    return reason


@dataclass(frozen=True)
class AgentInvocation:
    """Per-call inputs that are independent of a Web or Conversation adapter."""

    state: Dict[str, Any]
    entry_agent: str = "lead_agent"
    user_id: Optional[str] = None
    available_skills: Sequence[Dict[str, Any]] = ()


@dataclass(frozen=True)
class EngineOutcome:
    """Mutable engine state plus the factual reason execution stopped."""

    state: Dict[str, Any]
    stop_reason: StopReason


class AgentRuntime:
    """Run one invocation without making any finalization decision."""

    def __init__(
        self,
        *,
        agents: Mapping[str, Any],
        tools: Mapping[str, Any],
        effective_toolsets: Mapping[str, Any],
        timeout: Optional[float] = None,
    ) -> None:
        self._agents = dict(agents)
        self._tools = dict(tools)
        self._effective_toolsets = dict(effective_toolsets)
        self._timeout = timeout

    async def run(
        self,
        invocation: AgentInvocation,
        *,
        hooks: RuntimeHooks,
        event_sink: Optional[EventSink] = None,
        artifact_service: Optional[Any] = None,
        sandbox_session: Optional[Any] = None,
    ) -> EngineOutcome:
        state = invocation.state
        timeout = config.EXECUTION_TIMEOUT if self._timeout is None else self._timeout
        try:
            async with asyncio.timeout(timeout):
                final_state = await execute_loop(
                    state=state,
                    agents=self._agents,
                    tools=self._tools,
                    effective_toolsets=self._effective_toolsets,
                    hooks=hooks,
                    artifact_service=artifact_service,
                    emit=event_sink,
                    sandbox_session=sandbox_session,
                    available_skills=list(invocation.available_skills),
                    user_id=invocation.user_id,
                    entry_agent=invocation.entry_agent,
                )
            return EngineOutcome(
                state=final_state,
                # A normal loop return is COMPLETE unless the loop recorded a
                # more specific terminal first.
                stop_reason=stop_execution(final_state, StopReason.COMPLETE),
            )
        except TimeoutError:
            logger.warning(
                "Agent runtime timed out after %ss for %s",
                timeout,
                state.get("message_id", "(unknown)"),
            )
            stop_execution(state, StopReason.TIMEOUT, replace=True)
            self._finalize_metrics_once(state, "timeout")
            return EngineOutcome(state=state, stop_reason=StopReason.TIMEOUT)
        except asyncio.CancelledError:
            # Preserve a semantic reason already recorded synchronously by the
            # loop.  Otherwise this is infrastructure cancellation (lease fence,
            # shutdown, supervisor cancellation), returned so the caller can
            # drain the child and finalize the accumulated state exactly once.
            recorded = get_stop_reason(state)
            if recorded is not None and recorded is not StopReason.COMPLETE:
                self._finalize_metrics_once(state, recorded.value)
                return EngineOutcome(state=state, stop_reason=recorded)

            logger.warning(
                "Agent runtime cancelled externally for %s; returning partial state",
                state.get("message_id", "(unknown)"),
            )
            stop_execution(state, StopReason.EXTERNAL_CANCEL, replace=True)
            self._finalize_metrics_once(state, "external cancel")
            return EngineOutcome(
                state=state,
                stop_reason=StopReason.EXTERNAL_CANCEL,
            )
        except Exception as exc:
            logger.exception("Agent runtime failed: %s", exc)
            stop_execution(state, StopReason.ERROR, replace=True)
            state["response"] = "Execution failed unexpectedly. Please retry."
            state["error_detail"] = {
                "error": str(exc),
                "agent": state.get("current_agent"),
                "request_id": get_request_id() or None,
            }
            self._finalize_metrics_once(state, "error")
            return EngineOutcome(state=state, stop_reason=StopReason.ERROR)

    @staticmethod
    def _finalize_metrics_once(state: Dict[str, Any], path: str) -> None:
        metrics = state.get("execution_metrics")
        if not isinstance(metrics, dict) or metrics.get("completed_at") is not None:
            return
        try:
            from core.execution.engine import finalize_metrics

            finalize_metrics(metrics)
        except Exception:
            logger.exception("finalize_metrics failed on AgentRuntime %s path", path)


__all__ = [
    "AgentInvocation",
    "AgentRuntime",
    "EngineOutcome",
    "EventSink",
    "RuntimeHooks",
    "StopReason",
    "get_stop_reason",
    "stop_execution",
]

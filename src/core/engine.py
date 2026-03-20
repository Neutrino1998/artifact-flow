"""
执行引擎 — Pi-style 扁平 while loop

设计文档 §执行引擎设计方向：
- 唯一的抽象是 context 构建
- call_llm → parse_tool_calls → 串行执行 → route → repeat
- Interrupt = asyncio.Event（in-memory await）
- 多工具支持（parse_tool_calls 返回列表，串行执行）
- Tool limit → 注入 system message 提醒总结
"""

import asyncio
from typing import Dict, Any, Optional, Callable, Awaitable, List, Tuple, TypedDict
from datetime import datetime

from core.events import StreamEventType, ExecutionEvent
from core.context_manager import ContextManager
from tools.xml_parser import parse_tool_calls
from tools.base import ToolPermission, ToolResult
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


# ============================================================
# ExecutionMetrics — 请求级可观测性指标
# ============================================================

class TokenUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class MetricsEvent(TypedDict, total=False):
    type: str              # "agent_complete" | "tool_complete"
    # agent_complete fields
    agent: str
    model: str
    token_usage: TokenUsage
    duration_ms: int
    started_at: str
    completed_at: str
    # tool_complete fields
    tool: str
    success: bool


class ExecutionMetrics(TypedDict):
    started_at: str
    completed_at: Optional[str]
    total_duration_ms: Optional[int]
    last_token_usage: Optional[TokenUsage]
    last_context_chars: int
    total_token_usage: TokenUsage
    events: List[MetricsEvent]


def create_initial_metrics() -> ExecutionMetrics:
    return {
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "total_duration_ms": None,
        "last_token_usage": None,
        "last_context_chars": 0,
        "total_token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "events": [],
    }


def finalize_metrics(metrics: ExecutionMetrics) -> None:
    completed_at = datetime.now()
    metrics["completed_at"] = completed_at.isoformat()
    started_at = datetime.fromisoformat(metrics["started_at"])
    metrics["total_duration_ms"] = int((completed_at - started_at).total_seconds() * 1000)


def append_metrics_event(metrics: ExecutionMetrics, event: MetricsEvent) -> None:
    """Append a metrics event and update token usage aggregates for agent_complete events."""
    metrics["events"].append(event)
    if event.get("type") == "agent_complete":
        usage = event.get("token_usage")
        if usage:
            metrics["last_token_usage"] = usage
            total = metrics["total_token_usage"]
            total["input_tokens"] += usage.get("input_tokens", 0)
            total["output_tokens"] += usage.get("output_tokens", 0)
            total["total_tokens"] += usage.get("total_tokens", 0)


# ============================================================
# 执行状态
# ============================================================

def create_initial_state(
    task: str,
    session_id: str,
    message_id: str,
    conversation_history: List[Dict[str, str]],
    always_allowed_tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """创建初始执行状态"""
    return {
        "current_task": task,
        "session_id": session_id,
        "message_id": message_id,
        "conversation_history": conversation_history,
        "completed": False,
        "error": False,
        "current_agent": "lead_agent",
        "always_allowed_tools": list(always_allowed_tools) if always_allowed_tools else [],
        "events": [],
        "execution_metrics": create_initial_metrics(),
        "response": "",
    }


# emit callback type: async (event_dict) -> None
# Execution always runs to completion regardless of SSE client state.
EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]


async def execute_loop(
    state: Dict[str, Any],
    agents: Dict[str, Any],  # {name: AgentConfig}
    tools: Dict[str, Any],   # {name: BaseTool}
    task_manager: Any,        # TaskManager
    artifact_manager: Optional[Any] = None,
    emit: Optional[EmitFn] = None,
    permission_timeout: int = 300,
    context_max_chars: int = 240000,
    compaction_preserve_pairs: int = 2,
    tool_interaction_preserve: int = 6,
) -> Dict[str, Any]:
    """
    Pi-style 扁平 while loop 执行引擎

    Args:
        state: 执行状态（from create_initial_state）
        agents: {name: AgentConfig} 字典
        tools: {name: BaseTool} 字典（全局 + 请求级工具已合并）
        task_manager: TaskManager 实例（用于 interrupt 和 message queue）
        artifact_manager: ArtifactManager 实例（用于 artifacts 清单）
        emit: 事件推送回调（推 SSE）
        permission_timeout: 单次 permission 确认等待超时（秒），默认 300

    Returns:
        最终执行状态
    """
    from models.llm import astream_with_retry, format_messages_for_debug

    message_id = state["message_id"]
    tool_round_count: Dict[str, int] = {}  # per-agent tool round counter

    # 3a. 记录用户原始输入为事件（统一 context 构建路径）
    state["events"].append(ExecutionEvent(
        event_type=StreamEventType.USER_INPUT.value,
        agent_name="lead_agent",
        data={"content": state["current_task"]},
    ))

    # ── closures ──

    async def _emit(event_type: str, agent: Optional[str] = None, data: Any = None, *, sse_only: bool = False) -> None:
        """推送事件。sse_only=True 仅推 SSE 不入内存事件列表（如 llm_chunk）"""
        event_dict = {
            "type": event_type,
            "agent": agent,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }

        if not sse_only:
            state["events"].append(ExecutionEvent(
                event_type=event_type,
                agent_name=agent,
                data=data,
            ))

        if emit:
            await emit(event_dict)

    def _resolve_tool(name: str):
        """从合并后的 tools dict 查找工具"""
        return tools.get(name)

    async def _build_context(agent_name: str, agent_config) -> list:
        """drain messages → artifacts 清单 → ContextManager.build → tool limit 注入"""
        if current_agent_name == "lead_agent":
            for msg in task_manager.drain_messages(message_id):
                wrapped = (
                    "[The user has injected a message during execution. "
                    "Consider this input and adjust your approach as needed.]\n"
                    + msg
                )
                await _emit(StreamEventType.QUEUED_MESSAGE.value, "lead_agent", {"content": wrapped})

        artifacts_inventory = None
        if artifact_manager and state.get("session_id"):
            try:
                artifact_manager.set_session(state["session_id"])
                artifacts_inventory = await artifact_manager.list_artifacts(
                    session_id=state["session_id"],
                    include_content=True,
                )
            except Exception as e:
                logger.warning(f"Failed to get artifacts inventory: {e}")

        context = ContextManager.build(
            state=state,
            agent_config=agent_config,
            agents=agents,
            tools=tools,
            artifact_manager=artifact_manager,
            artifacts_inventory=artifacts_inventory,
            context_max_chars=context_max_chars,
            compaction_preserve_pairs=compaction_preserve_pairs,
            tool_interaction_preserve=tool_interaction_preserve,
        )

        messages = context.messages

        if tool_round_count.get(agent_name, 0) >= agent_config.max_tool_rounds:
            messages.append({
                "role": "system",
                "content": "You have reached the maximum number of tool calls. "
                           "Please summarize your findings and provide a final response."
            })

        return messages

    async def _call_llm(messages: list, agent_name: str, model: str) -> Optional[Tuple[str, Optional[str], dict]]:
        """
        流式调用 LLM，推送 llm_chunk / llm_complete，记录 metrics。

        Returns:
            (response_content, reasoning_content, token_usage) 或 None（LLM 出错，state 已设置）
        """
        llm_start_time = datetime.now()

        response_content = ""
        reasoning_content = None
        token_usage = {}

        try:
            async for chunk in astream_with_retry(messages, model=model):
                chunk_type = chunk.get("type")

                if chunk_type == "content":
                    response_content += chunk["content"]
                    await _emit(StreamEventType.LLM_CHUNK.value, agent_name, {
                        "content": response_content,
                    }, sse_only=True)

                elif chunk_type == "reasoning":
                    if reasoning_content is None:
                        reasoning_content = ""
                    reasoning_content += chunk["content"]
                    await _emit(StreamEventType.LLM_CHUNK.value, agent_name, {
                        "reasoning_content": reasoning_content,
                    }, sse_only=True)

                elif chunk_type == "usage":
                    token_usage = chunk["token_usage"]

                elif chunk_type == "final":
                    if not response_content and chunk.get("content"):
                        response_content = chunk["content"]
                    if not reasoning_content and chunk.get("reasoning_content"):
                        reasoning_content = chunk["reasoning_content"]
                    if not token_usage and chunk.get("token_usage"):
                        token_usage = chunk["token_usage"]

        except Exception as llm_error:
            logger.error(f"LLM call failed: {llm_error}")
            await _emit(StreamEventType.ERROR.value, agent_name, {
                "error": f"LLM call failed: {str(llm_error)}",
                "agent": agent_name,
            })
            state["completed"] = True
            state["error"] = True
            state["response"] = f"LLM call failed: {str(llm_error)}"
            return None

        llm_end_time = datetime.now()
        llm_duration_ms = int((llm_end_time - llm_start_time).total_seconds() * 1000)

        # Map LiteLLM keys (prompt_tokens/completion_tokens) to unified keys (input_tokens/output_tokens)
        normalized_usage = {
            "input_tokens": token_usage.get("prompt_tokens", 0),
            "output_tokens": token_usage.get("completion_tokens", 0),
            "total_tokens": token_usage.get("total_tokens", 0),
        }

        await _emit(StreamEventType.LLM_COMPLETE.value, agent_name, {
            "content": response_content,
            "reasoning_content": reasoning_content,
            "token_usage": normalized_usage,
            "model": model,
            "duration_ms": llm_duration_ms,
        })

        append_metrics_event(state["execution_metrics"], {
            "type": "agent_complete",
            "agent": agent_name,
            "model": model,
            "token_usage": normalized_usage,
            "duration_ms": llm_duration_ms,
            "started_at": llm_start_time.isoformat(),
            "completed_at": llm_end_time.isoformat(),
        })

        input_tokens = normalized_usage["input_tokens"]
        output_tokens = normalized_usage["output_tokens"]
        logger.debug(f"[{agent_name}] LLM Response (input: {input_tokens}, output: {output_tokens}):\n{response_content[:500]}")

        return response_content, reasoning_content, token_usage

    async def _handle_permission(tool_name: str, params: dict, agent_name: str, permission: ToolPermission) -> bool:
        """
        处理权限中断。

        Returns:
            True — approved, False — denied（含超时和客户端断开）
        """
        await _emit(StreamEventType.PERMISSION_REQUEST.value, agent_name, {
            "permission_level": permission.value,
            "tool": tool_name,
            "params": params,
        })

        interrupt = task_manager.create_interrupt(message_id, {
            "type": "tool_permission",
            "agent": agent_name,
            "tool_name": tool_name,
            "params": params,
            "permission_level": permission.value,
            "message": f"Tool '{tool_name}' requires {permission.value} permission",
        })

        try:
            await asyncio.wait_for(interrupt.event.wait(), timeout=permission_timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Permission timeout for tool '{tool_name}' after {permission_timeout}s, treating as denied")
            await _emit(StreamEventType.PERMISSION_RESULT.value, agent_name, {
                "approved": False, "tool": tool_name, "reason": "timeout",
            })
            return False

        resume_data = interrupt.resume_data or {}
        is_approved = resume_data.get("approved", False)

        await _emit(StreamEventType.PERMISSION_RESULT.value, agent_name, {
            "approved": is_approved, "tool": tool_name,
        })

        if not is_approved:
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "tool": tool_name, "params": params,
            })
            await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                "tool": tool_name, "success": False,
                "error": "Permission denied by user. You do not have permission to use this tool.",
                "duration_ms": 0,
            })
            return False

        if resume_data.get("always_allow", False):
            allowed = list(state.get("always_allowed_tools", []))
            if tool_name not in allowed:
                allowed.append(tool_name)
            state["always_allowed_tools"] = allowed
            logger.info(f"Tool '{tool_name}' added to always_allowed_tools")

        return True

    async def _execute_tools(tool_calls: list, agent_name: str, agent_config) -> None:
        """串行执行工具列表，处理权限中断和 subagent 切换。
        call_subagent 延后到最后执行，确保同一轮的常规工具不会被 break 跳过。
        """
        tool_calls = sorted(tool_calls, key=lambda tc: tc.name == "call_subagent")
        for tool_call in tool_calls:
            if _check_cancelled():
                break

            # Parser 返回的解析错误 → 直接反馈给 agent
            if tool_call.error:
                await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                    "tool": tool_call.name,
                    "success": False,
                    "error": tool_call.error,
                    "duration_ms": 0,
                })
                tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                continue

            tool_name = tool_call.name
            params = tool_call.params

            # Agent 工具白名单校验
            if tool_name not in agent_config.tools:
                await _emit(StreamEventType.TOOL_START.value, agent_name, {
                    "tool": tool_name, "params": params,
                })
                await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                    "tool": tool_name, "success": False,
                    "error": f"Tool '{tool_name}' not available for '{agent_name}'",
                    "duration_ms": 0,
                })
                tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                continue

            # call_subagent 特殊处理
            if tool_name == "call_subagent":
                tool = _resolve_tool("call_subagent")
                if tool:
                    try:
                        result = await tool(**params)
                    except Exception as e:
                        logger.exception(f"call_subagent execution error: {e}")
                        await _emit(StreamEventType.TOOL_START.value, agent_name, {
                            "tool": "call_subagent", "params": params,
                        })
                        await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                            "tool": "call_subagent",
                            "success": False,
                            "error": str(e),
                            "duration_ms": 0,
                        })
                        tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                        continue
                    if result.success:
                        target_agent = params["agent_name"]
                        instruction = params["instruction"]

                        await _emit(StreamEventType.TOOL_START.value, agent_name, {
                            "tool": "call_subagent",
                            "params": {"agent_name": target_agent, "instruction": instruction},
                        })

                        # 注入 instruction 到 subagent 的事件流（仅内存，不推 SSE）
                        # context 构建按 agent_name 过滤时自然拿到 instruction
                        state["events"].append(ExecutionEvent(
                            event_type=StreamEventType.SUBAGENT_INSTRUCTION.value,
                            agent_name=target_agent,
                            data={"instruction": instruction},
                        ))

                        # tool_complete 在 subagent 完成后由 _complete_agent 路径追加
                        state["current_agent"] = target_agent
                        logger.info(f"Switching to subagent: {target_agent}")
                        tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                        break  # 跳出 tool_calls 循环，继续 while loop
                    else:
                        # 验证失败（如目标 agent 不存在），返回错误信息给 agent
                        await _emit(StreamEventType.TOOL_START.value, agent_name, {
                            "tool": "call_subagent", "params": params,
                        })
                        await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                            "tool": "call_subagent",
                            "success": False,
                            "error": result.error or "call_subagent validation failed",
                            "duration_ms": 0,
                        })
                        tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                        continue

            # 获取工具
            tool = _resolve_tool(tool_name)
            if not tool:
                await _emit(StreamEventType.TOOL_START.value, agent_name, {
                    "tool": tool_name, "params": params,
                })
                await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                    "tool": tool_name, "success": False,
                    "error": f"Tool '{tool_name}' not found",
                    "duration_ms": 0,
                })
                tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                continue

            # 权限检查（per-agent 权限覆盖）
            agent_perm_str = agent_config.tools.get(tool_name, tool.permission.value)
            effective_permission = ToolPermission(agent_perm_str)
            if effective_permission == ToolPermission.CONFIRM:
                if tool_name not in state.get("always_allowed_tools", []):
                    approved = await _handle_permission(tool_name, params, agent_name, effective_permission)
                    if not approved:
                        tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1
                        continue

            # 执行工具
            tool_start_time = datetime.now()
            await _emit(StreamEventType.TOOL_START.value, agent_name, {
                "tool": tool_name, "params": params,
            })

            try:
                tool_result = await tool(**params)
            except Exception as e:
                logger.exception(f"Tool '{tool_name}' execution error: {e}")
                tool_result = ToolResult(success=False, error=str(e))

            tool_end_time = datetime.now()
            tool_duration_ms = int((tool_end_time - tool_start_time).total_seconds() * 1000)

            await _emit(StreamEventType.TOOL_COMPLETE.value, agent_name, {
                "tool": tool_name,
                "success": tool_result.success,
                "result_data": tool_result.data if tool_result.success else None,
                "error": tool_result.error if not tool_result.success else None,
                "duration_ms": tool_duration_ms,
                "params": params,
            })

            append_metrics_event(state["execution_metrics"], {
                "type": "tool_complete",
                "tool": tool_name,
                "success": tool_result.success,
                "duration_ms": tool_duration_ms,
                "started_at": tool_start_time.isoformat(),
                "completed_at": tool_end_time.isoformat(),
                "agent": agent_name,
            })

            tool_round_count[agent_name] = tool_round_count.get(agent_name, 0) + 1

    def _check_cancelled() -> bool:
        if task_manager.is_cancelled(message_id):
            state["completed"] = True
            state["cancelled"] = True
            state["response"] = state.get("response", "") or ""
            return True
        return False

    # ── main loop ──

    try:
        while not state["completed"]:
            if _check_cancelled():
                break

            current_agent_name = state["current_agent"]
            agent_config = agents.get(current_agent_name)
            if not agent_config:
                logger.error(f"Agent '{current_agent_name}' not found")
                state["error"] = True
                state["response"] = f"Agent '{current_agent_name}' not found"
                await _emit(StreamEventType.ERROR.value, current_agent_name, {
                    "error": f"Agent '{current_agent_name}' not found"
                })
                break

            messages = await _build_context(current_agent_name, agent_config)

            # 记录 context 字符数（与 context_manager 的 len() 计算方式一致）
            last_context_chars = sum(len(m.get("content", "")) for m in messages)
            state["execution_metrics"]["last_context_chars"] = last_context_chars

            await _emit(StreamEventType.AGENT_START.value, current_agent_name, {
                "agent": current_agent_name,
            })

            logger.debug(f"[{current_agent_name}] Messages:\n{format_messages_for_debug(messages)}")

            # 调用 LLM（流式）
            llm_result = await _call_llm(messages, current_agent_name, agent_config.model)
            if llm_result is None:
                break

            response_content, reasoning_content, token_usage = llm_result

            # 解析工具调用
            tool_calls = parse_tool_calls(response_content)

            if not tool_calls:
                # Lead 无工具调用但队列中有待处理消息 → 不退出，继续循环
                # 这处理了 inject 消息在最后一次 LLM 调用期间到达的情况
                if current_agent_name == "lead_agent":
                    pending = task_manager.drain_messages(message_id)
                    if pending:
                        for msg in pending:
                            wrapped = (
                                "[The user has injected a message during execution. "
                                "Consider this input and adjust your approach as needed.]\n"
                                + msg
                            )
                            await _emit(StreamEventType.QUEUED_MESSAGE.value, "lead_agent", {"content": wrapped})
                        continue  # 回到 while loop 顶部，下次 _build_context 会看到新事件

                # 无待处理消息 → 正常完成当前 agent
                previous_agent = state["current_agent"]
                _complete_agent(state, current_agent_name, response_content)
                tool_round_count.pop(current_agent_name, None)

                await _emit(StreamEventType.AGENT_COMPLETE.value, current_agent_name, {
                    "agent": current_agent_name,
                    "content": response_content,
                })

                # Subagent 完成 → 追加 call_subagent 的 tool_complete，
                # 把 subagent 的 response 作为 result 传回给 lead
                if previous_agent != "lead_agent" and state["current_agent"] == "lead_agent":
                    subagent_xml = (
                        f'<subagent_result agent="{previous_agent}">'
                        f'\n{response_content}'
                        f'\n</subagent_result>'
                    )
                    await _emit(StreamEventType.TOOL_COMPLETE.value, "lead_agent", {
                        "tool": "call_subagent",
                        "success": True,
                        "result_data": subagent_xml,
                        "duration_ms": 0,
                    })

                continue

            # 串行执行工具
            await _execute_tools(tool_calls, current_agent_name, agent_config)

    except Exception as e:
        logger.exception(f"Execution loop error: {e}")
        await _emit(StreamEventType.ERROR.value, state.get("current_agent"), {
            "error": str(e),
            "agent": state.get("current_agent"),
        })
        state["error"] = True
        state["response"] = f"Execution failed: {str(e)}"

    # 完成 metrics
    finalize_metrics(state["execution_metrics"])

    return state


def _complete_agent(
    state: Dict[str, Any],
    agent_name: str,
    response_content: str,
) -> None:
    """
    完成当前 agent

    - lead 无工具调用 → completed = True
    - subagent 无工具调用 → 打包 tool_result 切回 lead
    """
    if agent_name == "lead_agent":
        state["completed"] = True
        state["response"] = response_content
        logger.info("Lead agent completed, execution done")
    else:
        # Subagent 完成 → 切回 lead
        # subagent 的响应作为 call_subagent 的 tool_result 返回给 lead
        state["current_agent"] = "lead_agent"
        logger.info(f"Subagent {agent_name} completed, switching back to lead_agent")



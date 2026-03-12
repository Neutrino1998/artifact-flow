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
from typing import Dict, Any, Optional, Callable, Awaitable, List
from datetime import datetime

from core.events import (
    StreamEventType, ExecutionEvent,
    append_agent_execution, append_tool_call, finalize_metrics,
)
from core.context_manager import ContextManager
from core.state import create_initial_state
from tools.xml_parser import parse_tool_calls
from tools.base import ToolPermission, ToolResult
from tools.prompt_generator import format_result
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# emit callback type: async (event_dict) -> None
# Execution always runs to completion regardless of SSE client state.
EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]


async def execute_loop(
    state: Dict[str, Any],
    agents: Dict[str, Any],  # {name: AgentConfig}
    tool_registry: Any,      # ToolRegistry
    task_manager: Any,        # TaskManager
    artifact_manager: Optional[Any] = None,
    emit: Optional[EmitFn] = None,
    permission_timeout: int = 300,
    request_tools: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Pi-style 扁平 while loop 执行引擎

    Args:
        state: 执行状态（from create_initial_state）
        agents: {name: AgentConfig} 字典
        tool_registry: ToolRegistry 实例
        task_manager: TaskManager 实例（用于 interrupt 和 message queue）
        artifact_manager: ArtifactManager 实例（用于 artifacts 清单）
        emit: 事件推送回调（推 SSE）
        permission_timeout: 单次 permission 确认等待超时（秒），默认 300
        request_tools: 请求级工具 {name: BaseTool}，优先于 tool_registry

    Returns:
        最终执行状态
    """
    from models.llm import create_llm

    message_id = state["message_id"]
    tool_round_count: Dict[str, int] = {}  # per-agent tool round counter

    async def _emit(event_type: str, agent: Optional[str] = None, data: Any = None, *, sse_only: bool = False) -> None:
        """
        推送事件

        Args:
            event_type: 事件类型
            agent: agent 名称
            data: 事件数据
            sse_only: 如果 True，仅推 SSE 不入内存事件列表（如 llm_chunk）
        """
        event_dict = {
            "type": event_type,
            "agent": agent,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }

        # 入内存事件列表（llm_chunk 除外）
        if not sse_only:
            state["events"].append(ExecutionEvent(
                event_type=event_type,
                agent_name=agent,
                data=data,
            ))

        # 推 SSE
        if emit:
            await emit(event_dict)

    def _resolve_tool(name: str):
        """先查 request_tools，再查 tool_registry"""
        if request_tools and name in request_tools:
            return request_tools[name]
        return tool_registry.get_tool(name)

    try:
        while not state["completed"]:
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

            # drain queued messages（非阻塞）
            queued = task_manager.drain_messages(message_id)
            if queued:
                state["queued_messages"].extend(queued)

            # ========== 构建 context ==========
            # 获取 artifacts 清单
            artifacts_inventory = None
            if artifact_manager and state.get("session_id"):
                try:
                    artifact_manager.set_session(state["session_id"])
                    artifacts_inventory = await artifact_manager.list_artifacts(
                        session_id=state["session_id"],
                        include_content=True,
                        content_preview_length=200,
                        full_content_for=["task_plan"]
                    )
                except Exception as e:
                    logger.warning(f"Failed to get artifacts inventory: {e}")

            context = ContextManager.build(
                state=state,
                agent_config=agent_config,
                agents=agents,
                tool_registry=tool_registry,
                artifact_manager=artifact_manager,
                artifacts_inventory=artifacts_inventory,
                request_tools=request_tools,
            )

            messages = context.messages

            # Tool limit 检查：注入 system message 提醒总结
            agent_rounds = tool_round_count.get(current_agent_name, 0)
            if agent_rounds >= agent_config.max_tool_rounds:
                messages.append({
                    "role": "system",
                    "content": "You have reached the maximum number of tool calls. "
                               "Please summarize your findings and provide a final response."
                })

            # ========== Agent start ==========
            await _emit(StreamEventType.AGENT_START.value, current_agent_name, {
                "agent": current_agent_name,
            })

            logger.debug(f"[{current_agent_name}] Messages:\n{_format_messages_for_debug(messages)}")

            # ========== 调用 LLM（流式） ==========
            llm = create_llm(model=agent_config.model)
            llm_start_time = datetime.now()

            response_content = ""
            reasoning_content = None
            token_usage = {}

            try:
                async for chunk in llm.astream_with_retry(messages):
                    chunk_type = chunk.get("type")

                    if chunk_type == "content":
                        response_content += chunk["content"]
                        await _emit(StreamEventType.LLM_CHUNK.value, current_agent_name, {
                            "content": chunk["content"],
                        }, sse_only=True)

                    elif chunk_type == "reasoning":
                        if reasoning_content is None:
                            reasoning_content = ""
                        reasoning_content += chunk["content"]

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
                await _emit(StreamEventType.ERROR.value, current_agent_name, {
                    "error": f"LLM call failed: {str(llm_error)}",
                    "agent": current_agent_name,
                })
                state["completed"] = True
                state["error"] = True
                state["response"] = f"LLM call failed: {str(llm_error)}"
                break

            llm_end_time = datetime.now()
            llm_duration_ms = int((llm_end_time - llm_start_time).total_seconds() * 1000)

            # LLM Complete 事件
            await _emit(StreamEventType.LLM_COMPLETE.value, current_agent_name, {
                "content": response_content,
                "reasoning_content": reasoning_content,
                "token_usage": token_usage,
            })

            # 更新 execution_metrics
            append_agent_execution(
                metrics=state["execution_metrics"],
                agent_name=current_agent_name,
                model=agent_config.model,
                token_usage={
                    "input_tokens": token_usage.get("prompt_tokens", 0),
                    "output_tokens": token_usage.get("completion_tokens", 0),
                    "total_tokens": token_usage.get("total_tokens", 0),
                },
                started_at=llm_start_time.isoformat(),
                completed_at=llm_end_time.isoformat(),
                llm_duration_ms=llm_duration_ms,
            )

            # 日志
            input_tokens = token_usage.get("prompt_tokens", 0)
            output_tokens = token_usage.get("completion_tokens", 0)
            logger.debug(f"[{current_agent_name}] LLM Response (input: {input_tokens}, output: {output_tokens}):\n{response_content[:500]}")

            # ========== 解析工具调用 ==========
            tool_calls = parse_tool_calls(response_content)

            if not tool_calls:
                # 无工具调用 → 完成当前 agent
                previous_agent = state["current_agent"]
                _complete_agent(state, current_agent_name, response_content)

                await _emit(StreamEventType.AGENT_COMPLETE.value, current_agent_name, {
                    "agent": current_agent_name,
                    "content": response_content,
                })

                # Subagent 完成 → 追加 call_subagent 的 tool_complete，
                # 把 subagent 的 response 作为 result 传回给 lead
                if previous_agent != "lead_agent" and state["current_agent"] == "lead_agent":
                    await _emit(StreamEventType.TOOL_COMPLETE.value, "lead_agent", {
                        "tool": "call_subagent",
                        "success": True,
                        "result_data": {"agent_name": previous_agent, "response": response_content},
                        "duration_ms": 0,
                    })

                continue

            # ========== 串行执行工具 ==========
            for tool_call in tool_calls:
                tool_name = tool_call.name
                params = tool_call.params

                # Agent 工具白名单校验
                if tool_name not in agent_config.tools:
                    await _emit(StreamEventType.TOOL_START.value, current_agent_name, {
                        "tool": tool_name, "params": params,
                    })
                    await _emit(StreamEventType.TOOL_COMPLETE.value, current_agent_name, {
                        "tool": tool_name, "success": False,
                        "error": f"Tool '{tool_name}' not available for '{current_agent_name}'",
                        "duration_ms": 0,
                    })
                    continue

                # call_subagent 特殊处理
                if tool_name == "call_subagent":
                    # 验证参数
                    tool = _resolve_tool("call_subagent")
                    if tool:
                        result = await tool(**params)
                        if result.success:
                            target_agent = result.data["agent_name"]
                            instruction = result.data["instruction"]

                            await _emit(StreamEventType.TOOL_START.value, current_agent_name, {
                                "tool": "call_subagent",
                                "params": {"agent_name": target_agent, "instruction": instruction},
                            })

                            # tool_complete 在 subagent 完成后由 _complete_agent 路径追加
                            # （包含 subagent 的 response 作为 result）

                            # 切换到 subagent
                            state["current_agent"] = target_agent
                            logger.info(f"Switching to subagent: {target_agent}")
                            break  # 跳出 tool_calls 循环，继续 while loop
                        else:
                            # 验证失败，当作普通工具错误
                            params = tool_call.params  # 重新设置 params
                            # fall through to normal tool execution below
                    # 如果验证失败，继续作为普通工具处理

                # 获取工具
                tool = _resolve_tool(tool_name)
                if not tool:
                    # 工具不存在，记录错误结果
                    await _emit(StreamEventType.TOOL_START.value, current_agent_name, {
                        "tool": tool_name, "params": params,
                    })
                    await _emit(StreamEventType.TOOL_COMPLETE.value, current_agent_name, {
                        "tool": tool_name, "success": False,
                        "error": f"Tool '{tool_name}' not found",
                        "duration_ms": 0,
                    })
                    continue

                # 权限检查（per-agent 权限覆盖）
                agent_perm_str = agent_config.tools.get(tool_name, tool.permission.value)
                effective_permission = ToolPermission(agent_perm_str)
                if effective_permission == ToolPermission.CONFIRM:
                    if tool_name not in state.get("always_allowed_tools", []):
                        # 需要用户确认 → interrupt
                        await _emit(StreamEventType.PERMISSION_REQUEST.value, current_agent_name, {
                            "permission_level": effective_permission.value,
                            "tool": tool_name,
                            "params": params,
                        })

                        # 创建 interrupt，等待用户确认
                        interrupt = task_manager.create_interrupt(message_id, {
                            "type": "tool_permission",
                            "agent": current_agent_name,
                            "tool_name": tool_name,
                            "params": params,
                            "permission_level": effective_permission.value,
                            "message": f"Tool '{tool_name}' requires {effective_permission.value} permission",
                        })

                        # 等待用户确认（超时 → error 终态）
                        try:
                            await asyncio.wait_for(interrupt.event.wait(), timeout=permission_timeout)
                        except asyncio.TimeoutError:
                            logger.error(f"Permission timeout for tool '{tool_name}' after {permission_timeout}s")
                            await _emit(StreamEventType.PERMISSION_RESULT.value, current_agent_name, {
                                "approved": False,
                                "tool": tool_name,
                                "reason": "timeout",
                            })
                            await _emit(StreamEventType.ERROR.value, current_agent_name, {
                                "error": f"Permission confirmation timed out after {permission_timeout}s for tool '{tool_name}'",
                                "agent": current_agent_name,
                            })
                            state["completed"] = True
                            state["error"] = True
                            state["response"] = f"Permission confirmation timed out for tool '{tool_name}'"
                            break
                        resume_data = interrupt.resume_data or {}
                        is_approved = resume_data.get("approved", False)

                        # 发送确认结果
                        await _emit(StreamEventType.PERMISSION_RESULT.value, current_agent_name, {
                            "approved": is_approved,
                            "tool": tool_name,
                        })

                        if not is_approved:
                            # 被拒绝，记录结果
                            await _emit(StreamEventType.TOOL_START.value, current_agent_name, {
                                "tool": tool_name, "params": params,
                            })
                            await _emit(StreamEventType.TOOL_COMPLETE.value, current_agent_name, {
                                "tool": tool_name, "success": False,
                                "error": "Permission denied by user. You do not have permission to use this tool.",
                                "duration_ms": 0,
                            })
                            continue

                        # always_allow
                        if resume_data.get("always_allow", False):
                            allowed = list(state.get("always_allowed_tools", []))
                            if tool_name not in allowed:
                                allowed.append(tool_name)
                            state["always_allowed_tools"] = allowed
                            logger.info(f"Tool '{tool_name}' added to always_allowed_tools")

                # 执行工具
                tool_start_time = datetime.now()
                await _emit(StreamEventType.TOOL_START.value, current_agent_name, {
                    "tool": tool_name, "params": params,
                })

                try:
                    tool_result = await tool(**params)
                except Exception as e:
                    logger.exception(f"Tool '{tool_name}' execution error: {e}")
                    tool_result = ToolResult(success=False, error=str(e))

                tool_end_time = datetime.now()
                tool_duration_ms = int((tool_end_time - tool_start_time).total_seconds() * 1000)

                await _emit(StreamEventType.TOOL_COMPLETE.value, current_agent_name, {
                    "tool": tool_name,
                    "success": tool_result.success,
                    "result_data": tool_result.data if tool_result.success else None,
                    "error": tool_result.error if not tool_result.success else None,
                    "duration_ms": tool_duration_ms,
                    "params": params,
                })

                # 更新 execution_metrics
                append_tool_call(
                    metrics=state["execution_metrics"],
                    tool_name=tool_name,
                    success=tool_result.success,
                    duration_ms=tool_duration_ms,
                    called_at=tool_start_time.isoformat(),
                    completed_at=tool_end_time.isoformat(),
                    agent=current_agent_name,
                )

                # 更新 tool round count
                tool_round_count[current_agent_name] = tool_round_count.get(current_agent_name, 0) + 1

            # agent_complete 事件（有工具调用的情况，一轮 LLM + tools 完成）
            # 注意：如果 agent 切换了（call_subagent），不发 agent_complete
            if state["current_agent"] == current_agent_name and not state["completed"]:
                # 还在当前 agent，继续 loop（工具调用后需要再次 LLM）
                pass

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


def _format_messages_for_debug(messages: list, max_content_len: int = 100000) -> str:
    """格式化消息用于调试输出"""
    lines = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        if not content:
            continue
        if len(content) > max_content_len:
            content = content[:max_content_len] + "..."
        lines.append(f"> {role}:")
        for line in content.split('\n'):
            lines.append(f"  {line}")
        lines.append("")
    return "\n".join(lines)

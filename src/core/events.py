"""
统一的事件类型定义

各层产生的事件：
- [Controller] 会话级别元数据和最终结果
- [Agent]      LLM 执行相关事件
- [Graph]      工具执行和权限相关事件
"""

from enum import Enum
from typing import TypedDict, Optional, Any, List
from dataclasses import dataclass, field
from datetime import datetime


class StreamEventType(Enum):
    """
    统一的执行事件类型

    产生位置：
    - [Controller] 会话级别元数据和最终结果
    - [Agent]      LLM 执行相关事件
    - [Graph]      工具执行和权限相关事件
    """

    # ========== Controller 层 ==========
    METADATA = "metadata"                # 会话元数据（conversation_id, thread_id）
    COMPLETE = "complete"                # 整体完成（含 execution_metrics）
    ERROR = "error"                      # 错误

    # ========== Agent 层 ==========
    AGENT_START = "agent_start"          # agent 开始执行
    LLM_CHUNK = "llm_chunk"              # LLM token 流
    LLM_COMPLETE = "llm_complete"        # LLM 单次调用完成
    AGENT_COMPLETE = "agent_complete"    # agent 本轮完成

    # ========== Graph 层 ==========
    TOOL_START = "tool_start"            # 工具开始执行
    TOOL_COMPLETE = "tool_complete"      # 工具执行完成
    PERMISSION_REQUEST = "permission_request"  # 请求权限确认
    PERMISSION_RESULT = "permission_result"    # 权限确认结果


@dataclass
class StreamEvent:
    """统一的流式事件"""
    type: StreamEventType
    timestamp: datetime = field(default_factory=datetime.now)

    # 可选字段，根据事件类型存在
    agent: Optional[str] = None          # agent 名称
    tool: Optional[str] = None           # 工具名称
    data: Any = None                     # 事件数据


# ============================================================
# ExecutionMetrics 相关类型定义
# ============================================================

class TokenUsage(TypedDict):
    """Token 使用统计"""
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ToolCallRecord(TypedDict):
    """单次工具调用记录"""
    tool_name: str
    success: bool
    duration_ms: int
    called_at: str      # ISO timestamp
    completed_at: str   # ISO timestamp
    agent: str          # 调用方 agent


class AgentExecutionRecord(TypedDict):
    """单次 agent 执行记录（一次 LLM 调用）"""
    agent_name: str
    model: str
    token_usage: TokenUsage
    llm_duration_ms: int
    started_at: str
    completed_at: str


class ExecutionMetrics(TypedDict):
    """
    请求级别的可观测性指标

    数据流：
    - Agent 执行完成 → append AgentExecutionRecord
    - Tool 执行完成 → append ToolCallRecord
    - 请求完成 → 设置 completed_at 和 total_duration_ms
    """
    started_at: str
    completed_at: Optional[str]
    total_duration_ms: Optional[int]
    agent_executions: List[AgentExecutionRecord]  # append-only
    tool_calls: List[ToolCallRecord]              # append-only


def create_initial_metrics() -> ExecutionMetrics:
    """创建初始的 ExecutionMetrics"""
    return {
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "total_duration_ms": None,
        "agent_executions": [],
        "tool_calls": []
    }


def finalize_metrics(metrics: ExecutionMetrics) -> None:
    """
    完成 metrics 记录（设置结束时间和总耗时）

    Args:
        metrics: 要完成的 ExecutionMetrics（会被原地修改）
    """
    completed_at = datetime.now()
    metrics["completed_at"] = completed_at.isoformat()

    # 计算总耗时
    started_at = datetime.fromisoformat(metrics["started_at"])
    duration_ms = int((completed_at - started_at).total_seconds() * 1000)
    metrics["total_duration_ms"] = duration_ms


def append_agent_execution(
    metrics: ExecutionMetrics,
    agent_name: str,
    model: str,
    token_usage: TokenUsage,
    started_at: str,
    completed_at: str,
    llm_duration_ms: int
) -> None:
    """
    追加 agent 执行记录

    Args:
        metrics: ExecutionMetrics（会被原地修改）
        其他参数: AgentExecutionRecord 的字段
    """
    record: AgentExecutionRecord = {
        "agent_name": agent_name,
        "model": model,
        "token_usage": token_usage,
        "llm_duration_ms": llm_duration_ms,
        "started_at": started_at,
        "completed_at": completed_at
    }
    metrics["agent_executions"].append(record)


def append_tool_call(
    metrics: ExecutionMetrics,
    tool_name: str,
    success: bool,
    duration_ms: int,
    called_at: str,
    completed_at: str,
    agent: str
) -> None:
    """
    追加工具调用记录

    Args:
        metrics: ExecutionMetrics（会被原地修改）
        其他参数: ToolCallRecord 的字段
    """
    record: ToolCallRecord = {
        "tool_name": tool_name,
        "success": success,
        "duration_ms": duration_ms,
        "called_at": called_at,
        "completed_at": completed_at,
        "agent": agent
    }
    metrics["tool_calls"].append(record)

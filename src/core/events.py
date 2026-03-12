"""
统一的事件类型定义

事件类型清单（设计文档 §事件类型清单）：
- agent_start      — agent 开始
- llm_chunk        — LLM token 流（仅推 SSE，不入内存事件列表）
- llm_complete     — LLM 调用完成
- tool_start       — 工具开始
- tool_complete    — 工具完成
- agent_complete   — agent 结束
- interrupt_pending — 需要用户确认
- interrupt_resolved — 确认结果
- execution_complete — 执行完成
- error            — 异常
"""

from enum import Enum
from typing import TypedDict, Optional, Any, List
from dataclasses import dataclass, field
from datetime import datetime


class StreamEventType(Enum):
    """
    统一的执行事件类型

    兼容旧 SSE 事件格式（value 不变），前端无需修改。
    """

    # ========== Controller 层 ==========
    METADATA = "metadata"                # 会话元数据（conversation_id, message_id）
    COMPLETE = "complete"                # 整体完成（含 execution_metrics）
    ERROR = "error"                      # 错误

    # ========== Agent 层 ==========
    AGENT_START = "agent_start"          # agent 开始执行
    LLM_CHUNK = "llm_chunk"              # LLM token 流（仅 SSE，不持久化）
    LLM_COMPLETE = "llm_complete"        # LLM 单次调用完成
    AGENT_COMPLETE = "agent_complete"    # agent 本轮完成

    # ========== 工具 / 权限层 ==========
    TOOL_START = "tool_start"            # 工具开始执行
    TOOL_COMPLETE = "tool_complete"      # 工具执行完成
    PERMISSION_REQUEST = "permission_request"  # 请求权限确认
    PERMISSION_RESULT = "permission_result"    # 权限确认结果


# ============================================================
# 内存事件（执行过程中累积，最终 batch write）
# ============================================================

@dataclass
class ExecutionEvent:
    """内存中的执行事件"""
    event_type: str          # StreamEventType.value
    agent_name: Optional[str] = None
    data: Any = None
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "agent_name": self.agent_name,
            "data": self.data,
            "created_at": self.created_at.isoformat(),
        }


# ============================================================
# ExecutionMetrics 相关类型定义（复用旧 events.py）
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
    """请求级别的可观测性指标"""
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
    """完成 metrics 记录"""
    completed_at = datetime.now()
    metrics["completed_at"] = completed_at.isoformat()

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
    """追加 agent 执行记录"""
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
    """追加工具调用记录"""
    record: ToolCallRecord = {
        "tool_name": tool_name,
        "success": success,
        "duration_ms": duration_ms,
        "called_at": called_at,
        "completed_at": completed_at,
        "agent": agent
    }
    metrics["tool_calls"].append(record)

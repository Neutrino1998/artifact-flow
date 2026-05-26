"""统一的事件类型定义"""

from enum import Enum
from typing import Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

from utils.time import utc_now


class StreamEventType(Enum):
    """
    统一的执行事件类型

    兼容旧 SSE 事件格式（value 不变），前端无需修改。
    """

    # ========== Controller 层 ==========
    METADATA = "metadata"                # 会话元数据（conversation_id, message_id）
    COMPLETE = "complete"                # 整体完成（含 execution_metrics）
    CANCELLED = "cancelled"              # 用户主动取消执行
    TIMED_OUT = "timed_out"              # 执行超时（EXECUTION_TIMEOUT，经 controller 既有 dispatcher 产出的一等终态）
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

    # ========== 输入 / 消息注入层 ==========
    USER_INPUT = "user_input"                        # 用户原始输入 → lead 首条消息
    QUEUED_MESSAGE = "queued_message"                # 执行中注入的用户消息 → lead
    SUBAGENT_INSTRUCTION = "subagent_instruction"    # lead → sub 的指令

    # ========== Compaction 层 ==========
    COMPACTION_START = "compaction_start"      # compaction 开始（持久化，便于 replay 看到"压缩进行中"指示）
    COMPACTION_SUMMARY = "compaction_summary"  # compaction 结果（持久化，作为历史 boundary）

    # ========== Runtime / 排队层 ==========
    # 任务进入 ExecutionRunner 的并发信号量等待队列时由 runner 推送（SSE-only，不持久化）。
    # 抵达 agent_start 后前端应自行清理 — 历史 replay 不需要这个事件。
    EXECUTION_QUEUED = "execution_queued"


# 终态事件类型 —— 执行到达最终状态（也是 stream 的停止条件）。
# 这是这个概念的**权威定义**(single source of truth)。core 层(decide_terminal /
# ensure_terminal)直接引用;传输/路由层故意保留各自的本地副本(不依赖执行语义),
# 由 tests/core/test_terminal_event_sync.py 交叉校验防漂移。新增终态类型时改这里,
# 测试会红线提示哪些本地副本忘了同步(P1#2: TIMED_OUT 当初就漏在传输/路由层)。
TERMINAL_EVENT_TYPES = frozenset({
    StreamEventType.COMPLETE.value,
    StreamEventType.CANCELLED.value,
    StreamEventType.TIMED_OUT.value,
    StreamEventType.ERROR.value,
})


# ============================================================
# 内存事件（执行过程中累积，最终 batch write）
# ============================================================

@dataclass
class ExecutionEvent:
    """内存中的执行事件"""
    event_type: str          # StreamEventType.value
    agent_name: Optional[str] = None
    data: Any = None
    event_id: Optional[str] = None  # stable dedupe key, set by controller before persist
    created_at: datetime = field(default_factory=utc_now)
    # True 表示从 DB 载入的历史事件（prior turn）；False 表示本轮新产生的事件。
    # 用于：持久化过滤（只写 False 的）、compaction preserve 边界（不跨轮）、
    # compaction 插入位置合法性校验（只能插在 False 段内）。
    is_historical: bool = False

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "agent_name": self.agent_name,
            "data": self.data,
            "created_at": self.created_at.isoformat(),
        }

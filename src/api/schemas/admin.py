"""
Admin-related Pydantic schemas

Defines request and response models for admin observability endpoints.
"""

from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field

from api.schemas.chat import MessageFeedbackResponse
from api.schemas.artifact import ArtifactSummary


class AdminConversationSummary(BaseModel):
    """Conversation summary for admin view"""
    id: str
    title: Optional[str] = None
    user_id: Optional[str] = None
    user_display_name: Optional[str] = None
    message_count: int = 0
    is_active: bool = False
    active_message_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AdminConversationListResponse(BaseModel):
    """GET /api/v1/admin/conversations response"""
    conversations: List[AdminConversationSummary]
    total: int
    has_more: bool


class AdminFeedbackItem(BaseModel):
    """One message-level feedback record in the admin read-only browser."""

    conversation_id: str
    conversation_title: Optional[str] = None
    user_id: Optional[str] = None
    user_display_name: Optional[str] = None
    message_id: str
    user_input: str
    feedback: MessageFeedbackResponse


class AdminFeedbackListResponse(BaseModel):
    feedback: List[AdminFeedbackItem]
    total: int
    has_more: bool


class AdminEventItem(BaseModel):
    """Single event in admin event timeline"""
    id: int
    event_id: Optional[str] = None  # 业务事件 id；用作 prompt / LLM call 重建锚
    event_type: str
    agent_name: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    created_at: datetime


class AdminUploadedFileRef(BaseModel):
    """Admin-visible attachment metadata after the active privacy projection."""

    id: Optional[str] = None
    filename: str
    content_accessible: bool = True


class AdminArtifactSummary(ArtifactSummary):
    """Artifact metadata plus the backend-authoritative admin read capability."""

    content_accessible: bool


class AdminArtifactListResponse(BaseModel):
    session_id: str
    artifacts: List[AdminArtifactSummary]


class AdminMessageGroup(BaseModel):
    """Events grouped by message"""
    message_id: str
    parent_id: Optional[str] = None  # 消息树父节点；前端据此渲染分支结构
    user_input: str
    response: Optional[str] = None
    created_at: datetime
    events: List[AdminEventItem]
    execution_metrics: Optional[Dict[str, Any]] = None
    feedback: Optional[MessageFeedbackResponse] = None
    uploaded_files: Optional[List[AdminUploadedFileRef]] = Field(
        None,
        description=(
            "Files attached to this turn, from Message.metadata_['uploaded_files']. "
            "Display-only and best-effort until the terminal DB refresh."
        ),
    )


class AdminPromptReconstructResponse(BaseModel):
    """重建某发 LLM 调用的 OpenAI-compatible messages 语义输入。

    has_reminder=False 表示该 agent_start 早于 reminder 持久化（只重建了 system_prompt +
    历史，无动态 reminder）。不包含未持久化的 native tools schema；messages 的 content
    可能是 str 或块列表（识图块降级为占位文本）。
    """
    conversation_id: str
    message_id: str
    agent_start_event_id: str
    agent_name: Optional[str] = None
    model: Optional[str] = None
    exposed_tool_names: Optional[List[str]] = Field(
        None,
        description=(
            "Exact native function names exposed to the anchored LLM invocation. "
            "None means the legacy event predates collection; an empty list means no "
            "tools were exposed. Full tool schemas are not persisted."
        ),
    )
    has_reminder: bool = False
    messages: List[Dict[str, Any]]


class AdminLlmCallReconstructResponse(AdminPromptReconstructResponse):
    """One persisted LLM request and its normalized response event payload."""

    llm_complete_event_id: str
    response: Dict[str, Any]


class AdminConversationEventsResponse(BaseModel):
    """GET /api/v1/admin/conversations/{conv_id}/events response"""
    conversation_id: str
    title: Optional[str] = None
    user_id: Optional[str] = None
    user_display_name: Optional[str] = None
    active_branch: Optional[str] = None
    is_active: bool = False
    active_message_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    messages: List[AdminMessageGroup]


class InstanceEventSourceStatus(BaseModel):
    """Readability state for one local diagnostic-file source."""

    configured: bool = True
    available: bool
    truncated: bool


class InstanceEventStackOwner(BaseModel):
    """One bounded task or thread stack captured by the watchdog."""

    name: str
    stack: List[str]
    done: Optional[bool] = None
    event_loop: Optional[bool] = None


class InstanceLoopLagMetrics(BaseModel):
    p50_ms: Optional[float] = None
    p99_ms: Optional[float] = None
    max_1m_ms: Optional[float] = None
    samples: Optional[int] = None


class InstanceProcessMetrics(BaseModel):
    rss_mb: Optional[float] = None
    cpu_pct: Optional[float] = None
    open_fds: Optional[int] = None


class InstanceDbPoolMetrics(BaseModel):
    in_use: Optional[int] = None
    size: Optional[int] = None
    overflow: Optional[int] = None


class InstanceRedisMetrics(BaseModel):
    used_mb: Optional[float] = None
    maxmemory_mb: Optional[float] = None


class InstanceEventMetricSnapshot(BaseModel):
    """Runtime sample immediately before or after a loop incident."""

    ts: Optional[str] = None
    loop_lag_ms: InstanceLoopLagMetrics = Field(default_factory=InstanceLoopLagMetrics)
    in_flight: Optional[int] = None
    tasks_long_running: Optional[int] = None
    process: InstanceProcessMetrics = Field(default_factory=InstanceProcessMetrics)
    db_pool: InstanceDbPoolMetrics = Field(default_factory=InstanceDbPoolMetrics)
    redis: InstanceRedisMetrics = Field(default_factory=InstanceRedisMetrics)


class InstanceDiagnosticEvent(BaseModel):
    """One normalized ERROR, hard wedge, or soft loop-lag record."""

    id: str
    type: Literal["error", "wedge", "loop_lag"]
    source: Literal["runtime_log", "loop_lag"]
    severity: Literal["warning", "error"]
    ts: str
    summary: str
    level: Optional[str] = None
    detail: Optional[str] = None
    location: Optional[str] = None
    lag_ms: Optional[float] = None
    lower_bound: bool = False
    warn_ms: Optional[float] = None
    request_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    active_message_ids: List[str] = Field(default_factory=list)
    instance_id: str
    tasks: List[InstanceEventStackOwner] = Field(default_factory=list)
    threads: List[InstanceEventStackOwner] = Field(default_factory=list)
    metrics_before: Optional[InstanceEventMetricSnapshot] = None
    metrics_after: Optional[InstanceEventMetricSnapshot] = None


class AdminInstanceEventsResponse(BaseModel):
    """GET /api/v1/admin/instances/{instance_id}/events response."""

    instance_id: str
    events: List[InstanceDiagnosticEvent]
    sources: Dict[str, InstanceEventSourceStatus]

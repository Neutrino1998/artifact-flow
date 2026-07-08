"""
Admin-related Pydantic schemas

Defines request and response models for admin observability endpoints.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class AdminConversationSummary(BaseModel):
    """Conversation summary for admin view"""
    id: str
    title: Optional[str] = None
    user_id: Optional[str] = None
    user_display_name: Optional[str] = None
    message_count: int = 0
    is_active: bool = False
    created_at: datetime
    updated_at: datetime


class AdminConversationListResponse(BaseModel):
    """GET /api/v1/admin/conversations response"""
    conversations: List[AdminConversationSummary]
    total: int
    has_more: bool


class AdminEventItem(BaseModel):
    """Single event in admin event timeline"""
    id: int
    event_id: Optional[str] = None  # 业务事件 id；agent_start 用它当 prompt 重建锚
    event_type: str
    agent_name: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    created_at: datetime


class AdminMessageGroup(BaseModel):
    """Events grouped by message"""
    message_id: str
    parent_id: Optional[str] = None  # 消息树父节点；前端据此渲染分支结构
    user_input: str
    response: Optional[str] = None
    created_at: datetime
    events: List[AdminEventItem]
    execution_metrics: Optional[Dict[str, Any]] = None


class AdminPromptReconstructResponse(BaseModel):
    """GET .../messages/{message_id}/reconstruct response — 重建某发 LLM 调用的完整 prompt。

    has_reminder=False 表示该 agent_start 早于 reminder 持久化（只重建了 system_prompt +
    历史，无动态 reminder）。messages 的 content 可能是 str 或块列表（识图块降级为占位文本）。
    """
    conversation_id: str
    message_id: str
    agent_start_event_id: str
    agent_name: Optional[str] = None
    has_reminder: bool = False
    messages: List[Dict[str, Any]]


class AdminConversationEventsResponse(BaseModel):
    """GET /api/v1/admin/conversations/{conv_id}/events response"""
    conversation_id: str
    title: Optional[str] = None
    user_id: Optional[str] = None
    user_display_name: Optional[str] = None
    active_branch: Optional[str] = None
    is_active: bool = False
    created_at: datetime
    updated_at: datetime
    messages: List[AdminMessageGroup]

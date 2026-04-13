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
    event_type: str
    agent_name: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    created_at: datetime


class AdminMessageGroup(BaseModel):
    """Events grouped by message"""
    message_id: str
    user_input: str
    response: Optional[str] = None
    created_at: datetime
    events: List[AdminEventItem]
    execution_metrics: Optional[Dict[str, Any]] = None


class AdminConversationEventsResponse(BaseModel):
    """GET /api/v1/admin/conversations/{conv_id}/events response"""
    conversation_id: str
    title: Optional[str] = None
    messages: List[AdminMessageGroup]

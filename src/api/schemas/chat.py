"""
Chat-related Pydantic schemas

Defines request and response models for chat endpoints.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


# ============================================================
# Request Models
# ============================================================

class ChatRequest(BaseModel):
    """POST /api/v1/chat request body"""
    user_input: str = Field(..., description="User message content")
    conversation_id: Optional[str] = Field(None, description="Continue existing conversation")
    parent_message_id: Optional[str] = Field(None, description="Branch from specific message")


class InjectRequest(BaseModel):
    """POST /api/v1/chat/{conv_id}/inject request body"""
    content: str = Field(..., description="Message content to inject into the active execution")


class InjectResponse(BaseModel):
    """POST /api/v1/chat/{conv_id}/inject response"""
    message_id: str = Field(..., description="Active execution message ID that received the injection")
    stream_url: str = Field(..., description="Existing SSE stream URL (already connected, do not reconnect)")


class CancelResponse(BaseModel):
    """POST /api/v1/chat/{conv_id}/cancel response"""
    message_id: str = Field(..., description="Cancelled execution message ID")


class ResumeRequest(BaseModel):
    """POST /api/v1/chat/{conv_id}/resume request body"""
    message_id: str = Field(..., description="Message ID to resume")
    approved: bool = Field(..., description="Whether the permission was approved")
    always_allow: bool = Field(False, description="Always allow this tool for the rest of this execution")


# ============================================================
# Response Models
# ============================================================

class ChatResponse(BaseModel):
    """POST /api/v1/chat response"""
    conversation_id: str = Field(..., description="Conversation ID")
    message_id: str = Field(..., description="New message ID")
    stream_url: str = Field(..., description="SSE endpoint URL for streaming")


class ResumeResponse(BaseModel):
    """POST /api/v1/chat/{conv_id}/resume response"""
    stream_url: str = Field(..., description="New SSE endpoint URL")


class MessageResponse(BaseModel):
    """Message in conversation detail response"""
    id: str = Field(..., description="Message ID")
    parent_id: Optional[str] = Field(None, description="Parent message ID")
    user_input: str = Field(..., description="User message content")
    response: Optional[str] = Field(None, description="Assistant response")
    created_at: datetime = Field(..., description="Message creation time")
    children: List[str] = Field(default_factory=list, description="Child message IDs")
    execution_metrics: Optional[Dict[str, Any]] = Field(
        None,
        description="Turn-level metrics from Message.metadata_['execution_metrics']: started_at, completed_at, total_duration_ms, total_token_usage, etc.",
    )


class ConversationSummary(BaseModel):
    """Conversation summary in list response"""
    id: str = Field(..., description="Conversation ID")
    title: Optional[str] = Field(None, description="Conversation title")
    message_count: int = Field(0, description="Number of messages")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class ConversationListResponse(BaseModel):
    """GET /api/v1/chat response"""
    conversations: List[ConversationSummary] = Field(..., description="Conversation list")
    total: int = Field(..., description="Total count")
    has_more: bool = Field(..., description="Whether more results exist")


class ConversationDetailResponse(BaseModel):
    """GET /api/v1/chat/{conv_id} response"""
    id: str = Field(..., description="Conversation ID")
    title: Optional[str] = Field(None, description="Conversation title")
    active_branch: Optional[str] = Field(None, description="Current active branch message ID")
    messages: List[MessageResponse] = Field(..., description="All messages (flat array with tree structure)")
    session_id: str = Field(..., description="Associated artifact session ID")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")

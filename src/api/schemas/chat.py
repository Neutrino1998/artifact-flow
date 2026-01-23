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
    content: str = Field(..., description="User message content")
    conversation_id: Optional[str] = Field(None, description="Continue existing conversation")
    parent_message_id: Optional[str] = Field(None, description="Branch from specific message")


class ResumeRequest(BaseModel):
    """POST /api/v1/chat/{conv_id}/resume request body"""
    thread_id: str = Field(..., description="LangGraph thread ID")
    message_id: str = Field(..., description="Message ID to update")
    approved: bool = Field(..., description="Whether the permission was approved")


# ============================================================
# Response Models
# ============================================================

class ChatResponse(BaseModel):
    """POST /api/v1/chat response"""
    conversation_id: str = Field(..., description="Conversation ID")
    message_id: str = Field(..., description="New message ID")
    thread_id: str = Field(..., description="LangGraph thread ID")
    stream_url: str = Field(..., description="SSE endpoint URL for streaming")


class ResumeResponse(BaseModel):
    """POST /api/v1/chat/{conv_id}/resume response"""
    stream_url: str = Field(..., description="New SSE endpoint URL")


class MessageResponse(BaseModel):
    """Message in conversation detail response"""
    id: str = Field(..., description="Message ID")
    parent_id: Optional[str] = Field(None, description="Parent message ID")
    content: str = Field(..., description="User message content")
    response: Optional[str] = Field(None, description="Assistant response")
    created_at: datetime = Field(..., description="Message creation time")
    children: List[str] = Field(default_factory=list, description="Child message IDs")


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

"""
Chat-related Pydantic schemas

Defines request and response models for chat endpoints.
"""

from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config import config


# ============================================================
# Request Models
# ============================================================

class ChatRequest(BaseModel):
    """POST /api/v1/chat request body"""
    user_input: str = Field(..., max_length=config.MAX_MESSAGE_CHARS, description="User message content")
    conversation_id: Optional[str] = Field(
        None,
        min_length=1,
        description="Continue existing conversation",
    )
    parent_message_id: Optional[str] = Field(None, description="Branch from specific message")
    force_compact: bool = Field(
        False,
        description=(
            "Force a one-shot context compaction on this turn (user pressed 'compact'). "
            "The lead agent answers as usual, then compaction fires once regardless of the "
            "token threshold, folding this turn into the summary. Like file attachments, it "
            "relaxes the empty-input guard so a compact-only turn (no text) is allowed."
        ),
    )
    activate_skills: List[str] = Field(
        default_factory=list,
        description=(
            "Skill slugs the user activated for this turn (pressed a skill's button). Each "
            "visible skill's instructions are injected into this turn's context and its "
            "agent-disabled tools are enabled; the activation is sticky across the "
            "conversation (mirrors always-allowed tools). Invisible slugs are silently "
            "dropped. Like force_compact, it relaxes the empty-input guard so an "
            "activation-only turn (no text) is allowed."
        ),
    )
    referenced_artifact_ids: List[str] = Field(
        default_factory=list,
        max_length=config.MAX_CHAT_ATTACHMENTS,
        description=(
            "Existing user-upload artifact ids from this conversation that the user "
            "explicitly referenced for this turn. References prioritize those files but "
            "do not hide other session artifacts. A reference-only turn is allowed."
        ),
    )

    @field_validator("referenced_artifact_ids")
    @classmethod
    def normalize_referenced_artifact_ids(cls, value: List[str]) -> List[str]:
        unique: List[str] = []
        for artifact_id in value:
            if not artifact_id:
                raise ValueError("referenced artifact ids must not be empty")
            if artifact_id not in unique:
                unique.append(artifact_id)
        return unique

    @model_validator(mode="after")
    def references_require_existing_conversation(self):
        if self.referenced_artifact_ids and self.conversation_id is None:
            raise ValueError("referenced artifacts require an existing conversation")
        return self


class InjectRequest(BaseModel):
    """POST /api/v1/chat/{conv_id}/inject request body"""
    content: str = Field(..., max_length=config.MAX_MESSAGE_CHARS, description="Message content to inject into the active execution")


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
    call_id: str = Field(..., min_length=1, description="Native tool-call ID to resume")
    approved: bool = Field(..., description="Whether the permission was approved")
    always_allow: bool = Field(False, description="Always allow this tool for the rest of this execution")


MAX_BULK_DELETE_IDS = 200

FeedbackRating = Literal["positive", "negative"]
FeedbackTag = Literal[
    "resolved_problem",
    "followed_instructions",
    "high_quality",
    "fast_efficient",
    "helpful_initiative",
    "incorrect_incomplete",
    "failed_instructions",
    "biased_out_of_scope",
    "lost_context",
    "slow_or_broken",
    "safety_or_legal",
    "other",
]

POSITIVE_FEEDBACK_TAGS = {
    "resolved_problem",
    "followed_instructions",
    "high_quality",
    "fast_efficient",
    "helpful_initiative",
    "other",
}
NEGATIVE_FEEDBACK_TAGS = {
    "incorrect_incomplete",
    "failed_instructions",
    "biased_out_of_scope",
    "lost_context",
    "slow_or_broken",
    "safety_or_legal",
    "other",
}


class MessageFeedbackRequest(BaseModel):
    """Create or replace the current user's feedback for one assistant response."""

    rating: FeedbackRating
    tags: List[FeedbackTag] = Field(default_factory=list, max_length=7)
    detail: Optional[str] = Field(
        None, max_length=config.MESSAGE_FEEDBACK_MAX_DETAIL_CHARS
    )

    @field_validator("detail")
    @classmethod
    def normalize_detail(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_tags_for_rating(self):
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("feedback tags must be unique")
        allowed = (
            POSITIVE_FEEDBACK_TAGS
            if self.rating == "positive"
            else NEGATIVE_FEEDBACK_TAGS
        )
        if any(tag not in allowed for tag in self.tags):
            raise ValueError("feedback tag does not match rating")
        return self


class MessageFeedbackResponse(BaseModel):
    """Persisted current feedback for one assistant response."""

    model_config = ConfigDict(from_attributes=True)

    rating: FeedbackRating
    tags: List[FeedbackTag] = Field(default_factory=list)
    detail: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_persisted_tags(cls, value):
        return value or []


class BulkDeleteRequest(BaseModel):
    """POST /api/v1/chat/bulk-delete request body"""
    ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_BULK_DELETE_IDS,
        description=f"Conversation IDs to delete (1-{MAX_BULK_DELETE_IDS})",
    )


# ============================================================
# Response Models
# ============================================================

class ErrorResponse(BaseModel):
    """Standard FastAPI error response with a string detail."""
    detail: str = Field(..., description="Human-readable error detail")


class ChatResponse(BaseModel):
    """POST /api/v1/chat response"""
    conversation_id: str = Field(..., description="Conversation ID")
    message_id: str = Field(..., description="New message ID")
    stream_url: str = Field(..., description="SSE endpoint URL for streaming")


class ActiveStreamResponse(BaseModel):
    """GET /api/v1/chat/{conv_id}/active-stream response"""
    active: bool = Field(..., description="Whether the conversation has a reconnectable live stream")
    conversation_id: str = Field(..., description="Conversation ID")
    message_id: Optional[str] = Field(None, description="Active execution message ID, when active")
    stream_url: Optional[str] = Field(None, description="SSE endpoint URL, when active")


class ResumeResponse(BaseModel):
    """POST /api/v1/chat/{conv_id}/resume response"""
    stream_url: str = Field(..., description="New SSE endpoint URL")


class UploadedFileRef(BaseModel):
    """File the user attached to a message (display-only snapshot)"""
    id: str = Field(..., description="Artifact ID the upload was staged as")
    filename: str = Field(..., description="Original filename")


class ActivatedSkillRef(BaseModel):
    """Skill the user explicitly activated on one message (display snapshot)."""

    slug: str = Field(..., description="Skill slug resolved for this turn")
    name: str = Field(..., description="Skill display name frozen at activation time")


class ReferencedArtifactRef(BaseModel):
    """Existing uploaded artifact explicitly referenced on one message."""

    id: str = Field(..., description="Referenced artifact ID")
    filename: str = Field(..., description="Original filename frozen at send time")


class MessageResponse(BaseModel):
    """Message in conversation detail response"""
    id: str = Field(..., description="Message ID")
    parent_id: Optional[str] = Field(None, description="Parent message ID")
    user_input: str = Field(..., description="User message content")
    response: Optional[str] = Field(None, description="Assistant response")
    created_at: datetime = Field(..., description="Message creation time")
    children: List[str] = Field(default_factory=list, description="Child message IDs")
    feedback: Optional[MessageFeedbackResponse] = Field(
        None, description="Current user's feedback for this assistant response."
    )
    execution_metrics: Optional[Dict[str, Any]] = Field(
        None,
        description="Turn-level metrics from Message.metadata_['execution_metrics']: started_at, completed_at, total_duration_ms, total_token_usage, etc.",
    )
    uploaded_files: Optional[List[UploadedFileRef]] = Field(
        None,
        description="Files the user attached this turn, from Message.metadata_['uploaded_files']. Display-only (best-effort): absent for turns that failed before artifact flush.",
    )
    activated_skills: Optional[List[ActivatedSkillRef]] = Field(
        None,
        description="Skills the user explicitly activated on this turn, from Message.metadata_['activated_skills']. This is a per-message display snapshot, unlike cumulative active_skills; model-initiated read_skill calls are excluded.",
    )
    referenced_artifacts: Optional[List[ReferencedArtifactRef]] = Field(
        None,
        description=(
            "Existing user-upload artifacts explicitly referenced on this turn, from "
            "Message.metadata_['referenced_artifacts']. This is a per-message display snapshot."
        ),
    )
    active_skills: Optional[List[str]] = Field(
        None,
        description="Lead-agent skill slugs active as of this turn, projected from Message.metadata_['agent_progressive_state']['lead_agent']['active_skills']. The branch-tail message drives the activation picker. Absent/empty when no skills are active.",
    )


class ConversationSummary(BaseModel):
    """Conversation summary in list response"""
    id: str = Field(..., description="Conversation ID")
    title: Optional[str] = Field(None, description="Conversation title")
    message_count: int = Field(0, description="Number of messages")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")
    active_message_id: Optional[str] = Field(
        None,
        description=(
            "The message_id of the currently-running execution on this "
            "conversation, or null if no execution is in flight. Carries "
            "execution identity (not just a boolean) so the frontend can "
            "compare-and-clear on terminal events without an old turn's "
            "completion clobbering a freshly-started new turn's indicator."
        ),
    )
    upload_bytes: int = Field(
        0,
        description=(
            "Total stored attachment/blob bytes for this conversation "
            "(SUM of ArtifactBlob.size_bytes). Surfaced per-row in the list so the "
            "user can see which conversation is consuming storage and pick what to "
            "delete when over quota. Blob-only: text content and event history are "
            "NOT counted (deleting the conversation reclaims those too, so the "
            "displayed number understates what is freed)."
        ),
    )


class ConversationListResponse(BaseModel):
    """GET /api/v1/chat response"""
    conversations: List[ConversationSummary] = Field(..., description="Conversation list")
    total: int = Field(..., description="Total count")
    has_more: bool = Field(..., description="Whether more results exist")


class StorageUsageResponse(BaseModel):
    """GET /api/v1/chat/storage response — per-user attachment storage usage."""
    used_bytes: int = Field(
        ..., description="Total stored blob bytes across all the user's conversations."
    )
    quota_bytes: int = Field(
        ...,
        description=(
            "Per-user blob quota (ARTIFACT_USER_QUOTA_BYTES). An upload is rejected "
            "with 413 when used_bytes + incoming would exceed it. 0 = unlimited "
            "(quota disabled); the frontend should render the bar as unbounded."
        ),
    )


class ConversationDetailResponse(BaseModel):
    """GET /api/v1/chat/{conv_id} response"""
    id: str = Field(..., description="Conversation ID")
    title: Optional[str] = Field(None, description="Conversation title")
    active_branch: Optional[str] = Field(None, description="Current active branch message ID")
    messages: List[MessageResponse] = Field(..., description="All messages (flat array with tree structure)")
    session_id: str = Field(..., description="Associated artifact session ID")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class BulkDeleteFailedItem(BaseModel):
    """One failed item in BulkDeleteResponse.failed."""
    id: str = Field(..., description="Conversation ID that failed to delete")
    reason: str = Field(
        ..., description="Failure reason: 'not_found' or 'active_execution'"
    )


class BulkDeleteResponse(BaseModel):
    """POST /api/v1/chat/bulk-delete response"""
    deleted: List[str] = Field(..., description="Successfully deleted conversation IDs")
    failed: List[BulkDeleteFailedItem] = Field(..., description="Per-id failures")

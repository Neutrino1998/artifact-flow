"""
Pydantic Schemas

Request and response models for the API.
"""

from .chat import (
    ChatRequest,
    ChatResponse,
    ResumeRequest,
    ResumeResponse,
    ConversationListResponse,
    ConversationDetailResponse,
    ConversationSummary,
    MessageResponse,
)
from .artifact import (
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactSummary,
    VersionDetailResponse,
    VersionSummary,
)
from .auth import (
    LoginRequest,
    CreateUserRequest,
    UpdateUserRequest,
    UserInfo,
    LoginResponse,
    UserResponse,
    UserListResponse,
)
from .events import SSEEvent

__all__ = [
    # Chat schemas
    "ChatRequest",
    "ChatResponse",
    "ResumeRequest",
    "ResumeResponse",
    "ConversationListResponse",
    "ConversationDetailResponse",
    "ConversationSummary",
    "MessageResponse",
    # Artifact schemas
    "ArtifactListResponse",
    "ArtifactResponse",
    "ArtifactSummary",
    "VersionDetailResponse",
    "VersionSummary",
    # Auth schemas
    "LoginRequest",
    "CreateUserRequest",
    "UpdateUserRequest",
    "UserInfo",
    "LoginResponse",
    "UserResponse",
    "UserListResponse",
    # Event schemas
    "SSEEvent",
]

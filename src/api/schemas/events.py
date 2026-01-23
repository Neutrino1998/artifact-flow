"""
SSE Event schemas

Defines the structure for Server-Sent Events.
"""

from typing import Optional, Any, Dict
from datetime import datetime
from pydantic import BaseModel, Field

from core.events import StreamEventType


class SSEEvent(BaseModel):
    """
    Unified SSE event format

    Used for all streaming events from Agent/Graph/Controller layers.
    """
    type: str = Field(..., description="Event type from StreamEventType")
    timestamp: datetime = Field(default_factory=datetime.now, description="Event timestamp")
    agent: Optional[str] = Field(None, description="Agent name (for agent-related events)")
    tool: Optional[str] = Field(None, description="Tool name (for tool-related events)")
    data: Optional[Dict[str, Any]] = Field(None, description="Event data")

    @classmethod
    def from_dict(cls, event_dict: Dict[str, Any]) -> "SSEEvent":
        """Create SSEEvent from event dictionary"""
        return cls(
            type=event_dict.get("type", "unknown"),
            timestamp=datetime.fromisoformat(event_dict["timestamp"]) if "timestamp" in event_dict else datetime.now(),
            agent=event_dict.get("agent"),
            tool=event_dict.get("tool"),
            data=event_dict.get("data")
        )

    def to_sse_data(self) -> str:
        """Convert to SSE data format (JSON string)"""
        import json
        return json.dumps({
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "agent": self.agent,
            "tool": self.tool,
            "data": self.data
        }, ensure_ascii=False)

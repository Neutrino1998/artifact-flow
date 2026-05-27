"""
Meta / client-config Pydantic schemas

Backend-owned runtime constants the frontend needs. Single source of truth:
the frontend fetches these instead of redefining values that would drift from
src/config.py. Values are static for the session — fetch once, cache.
"""

from pydantic import BaseModel, Field


class ClientConfigResponse(BaseModel):
    """GET /api/v1/meta response — runtime constants for the frontend."""
    compaction_token_threshold: int = Field(
        ...,
        description=(
            "Token sum (a single LLM call's input+output) at which the engine "
            "auto-compacts. Used by the frontend as the context-usage gauge denominator "
            "so it doesn't hardcode a value that could drift from the server."
        ),
    )

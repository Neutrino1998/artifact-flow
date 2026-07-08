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
    lead_agent_model: str = Field(
        ...,
        description=(
            "Model identifier configured for the lead_agent (e.g. 'qwen3.7-max'). "
            "Surfaced in the composer so the user can see which model is driving the "
            "current conversation without digging into agent MD files."
        ),
    )
    max_upload_size: int = Field(
        ...,
        description=(
            "Per-file upload byte limit (MAX_UPLOAD_SIZE). The composer uses it to "
            "pre-reject an oversize file with instant feedback instead of staging + "
            "POSTing it for a backend 422. Backend stays authoritative; the batch "
            "TOTAL is capped separately at the proxy layer (not surfaced here — it "
            "lives in nginx/Caddy config, outside src/config.py)."
        ),
    )

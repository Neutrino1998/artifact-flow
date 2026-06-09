"""
Meta endpoint (GET /api/v1/meta) tests.

Single source of truth for backend runtime constants the frontend reads. The
endpoint is small (no business logic, no DB writes) but each field's contract
matters — the frontend hard-couples to the shape, and a silent drop / type
flip would only surface as a UI regression. These tests pin the shape.
"""

import pytest
from httpx import AsyncClient

from agents.loader import load_all_agents
from api import dependencies as deps
from config import config


# ============================================================
# Local fixture: ASGITransport skips lifespan, so init_globals() doesn't run
# and deps._agents stays None — get_agents() in the meta router would raise
# RuntimeError. Mirror the conftest pattern that pre-populates deps._db_manager:
# load agents once and stash on the module global for the test's duration.
# ============================================================


@pytest.fixture(autouse=True)
def loaded_agents():
    old = deps._agents
    deps._agents = load_all_agents()
    try:
        yield
    finally:
        deps._agents = old


# ============================================================
# Auth: meta is authenticated; anonymous gets 401
# ============================================================


@pytest.mark.asyncio
async def test_meta_requires_auth(anon_client: AsyncClient):
    resp = await anon_client.get("/api/v1/meta")
    assert resp.status_code == 401


# ============================================================
# Shape: every field the frontend reads exists with the right type
# ============================================================


@pytest.mark.asyncio
async def test_meta_returns_full_shape(client: AsyncClient):
    resp = await client.get("/api/v1/meta")
    assert resp.status_code == 200
    data = resp.json()

    # compaction_token_threshold — context-usage gauge denominator
    assert "compaction_token_threshold" in data
    assert isinstance(data["compaction_token_threshold"], int)
    assert data["compaction_token_threshold"] == config.COMPACTION_TOKEN_THRESHOLD

    # lead_agent_model — composer model badge
    assert "lead_agent_model" in data
    assert isinstance(data["lead_agent_model"], str)
    # Loaded from config/agents/lead_agent.md frontmatter `model:` — non-empty
    # guaranteed because AgentConfig defaults model to a literal even when the
    # MD omits it, and lead_agent.md sets it explicitly.
    assert len(data["lead_agent_model"]) > 0

    # max_upload_size — composer's per-file size pre-gate (mirrors MAX_UPLOAD_SIZE)
    assert "max_upload_size" in data
    assert isinstance(data["max_upload_size"], int)
    assert data["max_upload_size"] == config.MAX_UPLOAD_SIZE

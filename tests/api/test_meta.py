"""
Meta endpoint (GET /api/v1/meta) tests.

Single source of truth for backend runtime constants the frontend reads. The
endpoint is small (no business logic, no DB writes) but each field's contract
matters — the frontend hard-couples to the shape, and a silent drop / type
flip would only surface as a UI regression. These tests pin the shape.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from db.models import Agent


@pytest.fixture(autouse=True)
async def materialized_lead_agent(db_session: AsyncSession):
    """Meta must use the same DB agent row that turn execution hydrates."""
    db_session.add(
        Agent(
            name="lead_agent",
            description="test lead",
            model="db-lead-model",
            max_tool_rounds=3,
            internal=False,
            role_prompt="test",
            builtin_tools={},
            source="seeded",
            seed_hash="test",
        )
    )
    await db_session.commit()


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
    assert data["lead_agent_model"] == "db-lead-model"

    # max_upload_size — composer's per-file size pre-gate (mirrors MAX_UPLOAD_SIZE)
    assert "max_upload_size" in data
    assert isinstance(data["max_upload_size"], int)
    assert data["max_upload_size"] == config.MAX_UPLOAD_SIZE

    # max_private_skills — personal skill allowance (-1 unlimited / 0 closed)
    assert "max_private_skills" in data
    assert isinstance(data["max_private_skills"], int)
    assert data["max_private_skills"] == config.SKILL_USER_MAX_PRIVATE_COUNT


@pytest.mark.asyncio
async def test_meta_missing_materialized_lead_agent_is_500(
    client: AsyncClient,
    db_session: AsyncSession,
):
    lead = await db_session.get(Agent, "lead_agent")
    await db_session.delete(lead)
    await db_session.commit()

    response = await client.get("/api/v1/meta")
    assert response.status_code == 500
    assert response.json()["detail"] == "Client configuration is unavailable"

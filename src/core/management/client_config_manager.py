"""Aggregate frontend metadata from each field's authoritative source."""

from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from models.llm import get_compaction_threshold
from repositories.tool_registry_repo import ToolRegistryRepository


class ClientConfigInvariantError(Exception):
    """Required materialized runtime configuration is missing."""


class ClientConfigManager:
    def __init__(self, session: AsyncSession):
        self._registry = ToolRegistryRepository(session)

    async def get(self) -> dict:
        # Turns hydrate agents from the materialized DB registry. The UI model
        # badge must read that same row, not a per-worker MD snapshot.
        lead_agent = await self._registry.get_agent("lead_agent")
        if lead_agent is None:
            raise ClientConfigInvariantError(
                "Agent registry is missing required lead_agent"
            )
        return {
            "compaction_token_threshold": get_compaction_threshold(lead_agent.model),
            "lead_agent_model": lead_agent.model,
            "max_upload_size": config.MAX_UPLOAD_SIZE,
            "max_private_skills": config.SKILL_USER_MAX_PRIVATE_COUNT,
            "message_feedback_max_detail_chars": config.MESSAGE_FEEDBACK_MAX_DETAIL_CHARS,
        }

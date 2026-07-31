"""Backend adapter for shared bounded text matching plus update_artifact tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from rapidfuzz.distance import Levenshtein

from config import config
from utils.text_match import (
    FuzzyBail,
    FuzzyMatch,
    MatchInfo,
    MatchLimits,
    compute_update as _shared_compute_update,
    find_fuzzy_match as _shared_find_fuzzy_match,
    normalize_for_match as _normalize_for_match,
)
from tools.base import BaseTool, ToolPermission, ToolResult
from utils.logger import get_logger

if TYPE_CHECKING:
    from tools.builtin.artifact_service import ArtifactService

logger = get_logger("ArtifactFlow")


def _match_limits() -> MatchLimits:
    """Snapshot operator-configured limits for one matching invocation."""
    return MatchLimits(
        anchor_shingle_len=config.ANCHOR_SHINGLE_LEN,
        anchor_min_usable_len=config.ANCHOR_MIN_USABLE_LEN,
        anchor_max_occurrences=config.ANCHOR_MAX_OCCURRENCES,
        max_unique_centers=config.MAX_UNIQUE_CENTERS,
        max_fuzzy_wall_clock_ms=config.MAX_FUZZY_WALL_CLOCK_MS,
        fuzzy_max_l_dist=config.FUZZY_MAX_L_DIST,
        fuzzy_max_ratio=config.FUZZY_MAX_RATIO,
        max_fuzzy_old_str_len=config.MAX_FUZZY_OLD_STR_LEN,
    )


def find_fuzzy_match(old_str: str, content: str):
    """Compatibility wrapper over the environment-neutral matching core."""
    result = _shared_find_fuzzy_match(
        old_str,
        content,
        limits=_match_limits(),
        distance_fn=Levenshtein.distance,
    )
    if isinstance(result, FuzzyBail) and result.outcome == "bail_deadline":
        stats = result.fuzzy_stats or {}
        logger.warning(
            "Fuzzy match deadline exceeded (verify_calls=%d m=%d n=%d k=%d L=%d "
            "unique_centers=%d) — bailing loudly",
            stats.get("verify_calls", 0),
            stats.get("m", len(old_str)),
            stats.get("n", len(content)),
            stats.get("k", 0),
            stats.get("L", 0),
            stats.get("unique_centers", 0),
        )
    return result


def compute_update(content: str, old_str: str, new_str: str) -> MatchInfo:
    """Compatibility dispatcher preserving the update_artifact API."""
    info = _shared_compute_update(
        content,
        old_str,
        new_str,
        limits=_match_limits(),
        distance_fn=Levenshtein.distance,
    )
    if info.success and info.match_type in {"normalized", "fuzzy"}:
        logger.info(
            "%s match succeeded (similarity: %.1f%%)",
            info.match_type.capitalize(),
            (info.similarity or 0.0) * 100,
        )
    elif info.fuzzy_stats and info.fuzzy_stats.get("outcome") == "bail_deadline":
        stats = info.fuzzy_stats
        logger.warning(
            "Fuzzy match deadline exceeded (verify_calls=%d m=%d n=%d k=%d L=%d "
            "unique_centers=%d) — bailing loudly",
            stats.get("verify_calls", 0),
            stats.get("m", len(old_str)),
            stats.get("n", len(content)),
            stats.get("k", 0),
            stats.get("L", 0),
            stats.get("unique_centers", 0),
        )
    return info


# ============================================================
# Tool — XML 输出与 metadata 透传
# ============================================================


def _truncate_middle(text: str, max_len: int = 200) -> str:
    """Truncate long text keeping head and tail with '...' in between."""
    if len(text) <= max_len:
        return text
    half = (max_len - 5) // 2  # 5 chars for "\n...\n"
    return text[:half] + "\n...\n" + text[-half:]


class UpdateArtifactTool(BaseTool):
    """Targeted text replacement with three-layer matching.

    XML output mirrors the layer that hit:

    * exact      → ``<artifact version=...>``
    * normalized → adds ``normalized="X%"`` + ``<normalize_detail>`` block
                   (expected vs matched). Same shape as the fuzzy branch so
                   either layer surfaces a structured diff to the model.
    * fuzzy      → ``fuzzy="X%"`` + ``<fuzzy_detail>`` block

    ``ToolResult.metadata`` carries the manager's match info verbatim
    (including ``fuzzy_stats`` when Layer 2 ran). Identity is preserved
    — no field copy / rewrap — so the engine's ``tool_complete`` event
    inherits the same dict and downstream analytics can pull it back via
    ``data->'metadata'->'fuzzy_stats'``.
    """

    def __init__(self, service: Optional["ArtifactService"] = None):
        super().__init__(
            name="update_artifact",
            description=(
                "Update artifact content by replacing old text with new text "
                "(supports fuzzy matching). Use for small, targeted edits — make "
                "several small old_str/new_str replacements rather than one large one."
            ),
            permission=ToolPermission.AUTO,
        )
        self._service = service

    def set_service(self, service: "ArtifactService") -> None:
        self._service = service

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Artifact ID to update"},
                "old_str": {"type": "string", "description": "Text to be replaced"},
                "new_str": {"type": "string", "description": "New text to replace with"},
            },
            "required": ["id", "old_str", "new_str"],
            "additionalProperties": False,
        }

    async def execute(self, **params) -> ToolResult:
        if not self._service:
            return ToolResult(success=False, error="ArtifactService not configured")

        session_id = self._service.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message, match_info = await self._service.update_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            old_str=params["old_str"],
            new_str=params["new_str"],
        )

        # Surface fuzzy_stats even on failure so observability gets the bail
        # reason (covers all of bail_low_entropy / no_anchor / budget /
        # deadline / ambiguous / no_window).
        if not success:
            metadata: Dict[str, Any] = {}
            if match_info and "fuzzy_stats" in match_info:
                metadata["fuzzy_stats"] = match_info["fuzzy_stats"]
            return ToolResult(success=False, error=message, metadata=metadata)

        logger.info(message)

        memory = await self._service.get_artifact(session_id, params["id"])
        version = memory.current_version if memory else None
        match_type = (match_info or {}).get("match_type")

        if match_type in ("fuzzy", "normalized") and match_info:
            # Both layers carry an expected/matched diff worth surfacing
            # symmetrically. Use distinct attribute / block names so the
            # downstream model can tell which layer fired.
            similarity = f"{match_info['similarity']:.1%}"
            expected = _truncate_middle(match_info["expected_text"], 200)
            matched = _truncate_middle(match_info["matched_text"], 200)
            attr_name = "fuzzy" if match_type == "fuzzy" else "normalized"
            block_name = "fuzzy_detail" if match_type == "fuzzy" else "normalize_detail"
            xml = (
                f'<artifact version="{version}" {attr_name}="{similarity}">'
                f"\n  <id>{params['id']}</id>"
                f"\n  {message}"
                f"\n  <{block_name}>"
                f"\n    <expected>{expected}</expected>"
                f"\n    <matched>{matched}</matched>"
                f"\n  </{block_name}>"
                f"\n</artifact>"
            )
        else:
            xml = f'<artifact version="{version}"><id>{params["id"]}</id> {message}</artifact>'

        return ToolResult(success=True, data=xml, metadata=(match_info or {}))

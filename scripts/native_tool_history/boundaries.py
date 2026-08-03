"""Transactional boundary apply/verify for the stopped cutover database."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from db.models import Conversation, Message, MessageEvent

from .checkpoint import Checkpoint, CheckpointError, SelectedBoundary


SUMMARY_FRAME = (
    "[Prior conversation has been compacted into this summary. "
    "Treat it as your memory of earlier context and continue from here.]"
)
MIGRATION_MODEL = "native-history-migration"
COMPACTION_START = "compaction_start"
COMPACTION_SUMMARY = "compaction_summary"


class BoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoundaryApplyResult:
    total: int
    inserted: int
    already_present: int


@dataclass(frozen=True)
class BoundaryVerifyResult:
    total: int
    verified: int


def _event_id(boundary: SelectedBoundary, event_type: str) -> str:
    identity = json.dumps(
        [
            boundary.migration_id,
            boundary.conversation_id,
            boundary.leaf_message_id,
            boundary.summary_kind,
            event_type,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"nhm:{hashlib.sha256(identity).hexdigest()}"


def expected_boundary_events(boundary: SelectedBoundary) -> tuple[dict[str, Any], dict[str, Any]]:
    common = {
        "migration_id": boundary.migration_id,
        "summary_kind": boundary.summary_kind,
    }
    start = {
        "event_id": _event_id(boundary, COMPACTION_START),
        "message_id": boundary.leaf_message_id,
        "event_type": COMPACTION_START,
        "agent_name": "lead_agent",
        "data": {
            "last_input_tokens": 0,
            "last_output_tokens": 0,
            "forced": False,
            **common,
        },
    }
    summary = {
        "event_id": _event_id(boundary, COMPACTION_SUMMARY),
        "message_id": boundary.leaf_message_id,
        "event_type": COMPACTION_SUMMARY,
        "agent_name": "lead_agent",
        "data": {
            "success": True,
            "content": f"{SUMMARY_FRAME}\n\n{boundary.summary_content}",
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "duration_ms": 0,
            "model": MIGRATION_MODEL,
            "error": None,
            **common,
        },
    }
    return start, summary


def _normalize_backend_name(name: str) -> str:
    return "postgresql" if name in {"postgres", "postgresql"} else name


def source_database_fingerprint(database_url: str) -> str:
    """Identify the configured database target without retaining credentials."""
    url = make_url(database_url)
    backend = _normalize_backend_name(url.get_backend_name())
    # SQLite keeps the pure helper testable without a PostgreSQL service; the
    # operator CLI rejects it before calling this function.
    if backend not in {"postgresql", "sqlite"}:
        raise BoundaryError(
            "native history migration supports PostgreSQL source databases only"
        )
    database = url.database
    if backend == "sqlite" and database:
        database = str(Path(database).resolve())
    identity = json.dumps(
        {
            "backend": backend,
            "host": url.host.lower() if url.host else None,
            "port": url.port or (5432 if backend == "postgresql" else None),
            "database": database,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def assert_source_database(checkpoint: Checkpoint, migration_id: str, database_url: str) -> None:
    report = checkpoint.report(migration_id)
    expected_kind = _normalize_backend_name(str(report["source_database_kind"]))
    actual_kind = _normalize_backend_name(make_url(database_url).get_backend_name())
    if expected_kind != actual_kind:
        raise BoundaryError(
            f"checkpoint source database kind is {expected_kind!r}, "
            f"but target is {actual_kind!r}"
        )
    if report["source_database_fingerprint"] != source_database_fingerprint(database_url):
        raise BoundaryError("checkpoint belongs to a different source database target")


async def _assert_leaf_still_matches(session, boundary: SelectedBoundary) -> None:
    row = (
        await session.execute(
            select(Message.id, Conversation.active_branch)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.id == boundary.leaf_message_id,
                Message.conversation_id == boundary.conversation_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise BoundaryError(
            f"scanned leaf {boundary.leaf_message_id!r} no longer exists in "
            f"conversation {boundary.conversation_id!r}"
        )
    has_child = await session.scalar(
        select(exists().where(
            Message.conversation_id == boundary.conversation_id,
            Message.parent_id == boundary.leaf_message_id,
        ))
    )
    if has_child:
        raise BoundaryError(
            f"scanned message {boundary.leaf_message_id!r} is no longer a leaf; "
            "backend writers must remain stopped"
        )
    is_active = row.active_branch == boundary.leaf_message_id
    if is_active != boundary.is_active_branch:
        raise BoundaryError(
            f"active branch changed after scan for conversation "
            f"{boundary.conversation_id!r}"
        )


def _matches(row: MessageEvent | None, expected: dict[str, Any]) -> bool:
    return (
        row is not None
        and row.event_id == expected["event_id"]
        and row.message_id == expected["message_id"]
        and row.event_type == expected["event_type"]
        and row.agent_name == expected["agent_name"]
        and (row.data or {}) == expected["data"]
    )


async def apply_boundaries(
    engine: AsyncEngine,
    checkpoint: Checkpoint,
    migration_id: str,
) -> BoundaryApplyResult:
    report = checkpoint.report(migration_id)
    if not report["ready_for_apply"]:
        raise CheckpointError(
            "checkpoint is not ready for apply: "
            f"blocking={report['blocking']}"
        )
    boundaries = checkpoint.selected_boundaries(migration_id)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    inserted = 0
    already_present = 0

    for boundary in boundaries:
        start, summary = expected_boundary_events(boundary)
        event_ids = [start["event_id"], summary["event_id"]]
        async with sessions() as session:
            async with session.begin():
                await _assert_leaf_still_matches(session, boundary)
                existing = list((await session.execute(
                    select(MessageEvent)
                    .where(MessageEvent.event_id.in_(event_ids))
                    .order_by(MessageEvent.id)
                )).scalars())
                if not existing:
                    session.add_all([
                        MessageEvent(**start),
                        MessageEvent(**summary),
                    ])
                    inserted += 1
                    continue
                if len(existing) != 2:
                    raise BoundaryError(
                        f"boundary pair is incomplete for leaf "
                        f"{boundary.leaf_message_id!r}"
                    )
                by_id = {row.event_id: row for row in existing}
                if not _matches(by_id.get(start["event_id"]), start) or not _matches(
                    by_id.get(summary["event_id"]), summary
                ):
                    raise BoundaryError(
                        f"deterministic event_id collision or content drift for leaf "
                        f"{boundary.leaf_message_id!r}"
                    )
                already_present += 1

    checkpoint.set_run_status(migration_id, "applied")
    return BoundaryApplyResult(
        total=len(boundaries),
        inserted=inserted,
        already_present=already_present,
    )


async def verify_boundaries(
    engine: AsyncEngine,
    checkpoint: Checkpoint,
    migration_id: str,
) -> BoundaryVerifyResult:
    boundaries = checkpoint.selected_boundaries(migration_id)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    verified = 0

    for boundary in boundaries:
        start, summary = expected_boundary_events(boundary)
        async with sessions() as session:
            await _assert_leaf_still_matches(session, boundary)
            rows = list((await session.execute(
                select(MessageEvent)
                .where(MessageEvent.message_id == boundary.leaf_message_id)
                .order_by(MessageEvent.id)
            )).scalars())
        by_id = {row.event_id: row for row in rows if row.event_id}
        start_row = by_id.get(start["event_id"])
        summary_row = by_id.get(summary["event_id"])
        if start_row is None or summary_row is None:
            raise BoundaryError(
                f"boundary pair missing for leaf {boundary.leaf_message_id!r}"
            )
        if not _matches(start_row, start) or not _matches(summary_row, summary):
            raise BoundaryError(
                f"boundary pair content mismatch for leaf {boundary.leaf_message_id!r}"
            )
        if start_row.id >= summary_row.id:
            raise BoundaryError(
                f"boundary event order is invalid for leaf {boundary.leaf_message_id!r}"
            )
        successful_summaries = [
            row
            for row in rows
            if row.agent_name == "lead_agent"
            and row.event_type == COMPACTION_SUMMARY
            and (row.data or {}).get("success", True)
        ]
        if not successful_summaries or successful_summaries[-1].event_id != summary["event_id"]:
            raise BoundaryError(
                f"migration boundary is not the latest effective lead boundary on leaf "
                f"{boundary.leaf_message_id!r}"
            )
        verified += 1

    return BoundaryVerifyResult(total=len(boundaries), verified=verified)

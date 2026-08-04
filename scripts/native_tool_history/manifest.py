"""Read-only lead-history inventory for the fully stopped cutover database."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from db.models import Conversation, Message


SUMMARY_KINDS = frozenset({"semantic", "mechanical"})


class ManifestError(RuntimeError):
    """The stopped source database cannot be safely enumerated."""


@dataclass(frozen=True)
class SourceConversation:
    conversation_id: str
    active_branch: str | None


@dataclass(frozen=True)
class SourceMessage:
    message_id: str
    conversation_id: str
    parent_id: str | None


@dataclass(frozen=True)
class LeafManifest:
    conversation_id: str
    leaf_message_id: str
    is_active_branch: bool
    path_message_count: int


@dataclass(frozen=True)
class SummaryTask:
    conversation_id: str
    leaf_message_id: str
    summary_kind: str

    def __post_init__(self) -> None:
        if self.summary_kind not in SUMMARY_KINDS:
            raise ValueError(f"invalid summary kind: {self.summary_kind!r}")


@dataclass(frozen=True)
class ScanResult:
    conversations: int
    messages: int
    empty_conversations: int
    leaves: tuple[LeafManifest, ...]
    tasks: tuple[SummaryTask, ...]


def _resolve_depths(
    conversation_id: str,
    messages: dict[str, SourceMessage],
) -> dict[str, int]:
    """Resolve root→message depths and fail loudly on orphan/cycle corruption."""
    cache: dict[str, int] = {}
    for start in messages:
        if start in cache:
            continue
        chain: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        prefix_depth = 0
        while current is not None:
            if current in cache:
                prefix_depth = cache[current]
                break
            if current in positions:
                cycle = chain[positions[current]:] + [current]
                raise ManifestError(
                    f"conversation {conversation_id!r} contains a parent cycle: {cycle!r}"
                )
            message = messages.get(current)
            if message is None:
                raise ManifestError(
                    f"conversation {conversation_id!r} references missing parent "
                    f"message {current!r}"
                )
            positions[current] = len(chain)
            chain.append(current)
            current = message.parent_id

        for message_id in reversed(chain):
            prefix_depth += 1
            cache[message_id] = prefix_depth
    return cache


def build_manifest(
    conversations: Iterable[SourceConversation],
    messages: Iterable[SourceMessage],
) -> ScanResult:
    """Build the immutable stopped-database manifest and summary task roster."""
    conversation_rows = list(conversations)
    message_rows = list(messages)
    conversation_map = {row.conversation_id: row for row in conversation_rows}
    by_conversation: dict[str, dict[str, SourceMessage]] = defaultdict(dict)

    for message in message_rows:
        if message.conversation_id not in conversation_map:
            raise ManifestError(
                f"message {message.message_id!r} references missing conversation "
                f"{message.conversation_id!r}"
            )
        if message.message_id in by_conversation[message.conversation_id]:
            raise ManifestError(f"duplicate message id {message.message_id!r}")
        by_conversation[message.conversation_id][message.message_id] = message

    leaves: list[LeafManifest] = []
    tasks: list[SummaryTask] = []
    empty_conversations = 0

    for conversation in sorted(
        conversation_rows, key=lambda row: row.conversation_id
    ):
        conv_messages = by_conversation.get(conversation.conversation_id, {})
        if not conv_messages:
            empty_conversations += 1
            if conversation.active_branch is not None:
                raise ManifestError(
                    f"empty conversation {conversation.conversation_id!r} has active_branch "
                    f"{conversation.active_branch!r}"
                )
            continue

        if conversation.active_branch is None:
            raise ManifestError(
                f"conversation {conversation.conversation_id!r} has messages but no active_branch"
            )
        if conversation.active_branch not in conv_messages:
            raise ManifestError(
                f"conversation {conversation.conversation_id!r} has dangling active_branch "
                f"{conversation.active_branch!r}"
            )

        depths = _resolve_depths(conversation.conversation_id, conv_messages)
        parent_ids = {
            message.parent_id
            for message in conv_messages.values()
            if message.parent_id is not None
        }
        leaf_ids = sorted(set(conv_messages) - parent_ids)
        if not leaf_ids:
            raise ManifestError(
                f"conversation {conversation.conversation_id!r} has no leaf"
            )
        if conversation.active_branch not in leaf_ids:
            raise ManifestError(
                f"conversation {conversation.conversation_id!r} active_branch "
                f"{conversation.active_branch!r} is not a leaf"
            )

        for leaf_id in leaf_ids:
            is_active = leaf_id == conversation.active_branch
            leaves.append(LeafManifest(
                conversation_id=conversation.conversation_id,
                leaf_message_id=leaf_id,
                is_active_branch=is_active,
                path_message_count=depths[leaf_id],
            ))
            tasks.append(SummaryTask(
                conversation_id=conversation.conversation_id,
                leaf_message_id=leaf_id,
                summary_kind="mechanical",
            ))
            if is_active:
                tasks.append(SummaryTask(
                    conversation_id=conversation.conversation_id,
                    leaf_message_id=leaf_id,
                    summary_kind="semantic",
                ))

    leaves.sort(key=lambda leaf: (leaf.conversation_id, leaf.leaf_message_id))
    tasks.sort(key=lambda task: (
        task.conversation_id,
        task.leaf_message_id,
        task.summary_kind,
    ))
    return ScanResult(
        conversations=len(conversation_rows),
        messages=len(message_rows),
        empty_conversations=empty_conversations,
        leaves=tuple(leaves),
        tasks=tuple(tasks),
    )


async def scan_database(database_url: str) -> ScanResult:
    """Read the stopped source database without invoking runtime repositories."""
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            conversation_rows = (
                await session.execute(
                    select(Conversation.id, Conversation.active_branch)
                    .order_by(Conversation.id)
                )
            ).all()
            message_rows = (
                await session.execute(
                    select(Message.id, Message.conversation_id, Message.parent_id)
                    .order_by(Message.conversation_id, Message.id)
                )
            ).all()
    finally:
        await engine.dispose()

    return build_manifest(
        (
            SourceConversation(
                conversation_id=row.id,
                active_branch=row.active_branch,
            )
            for row in conversation_rows
        ),
        (
            SourceMessage(
                message_id=row.id,
                conversation_id=row.conversation_id,
                parent_id=row.parent_id,
            )
            for row in message_rows
        ),
    )

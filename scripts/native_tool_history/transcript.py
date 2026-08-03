"""Display-only branch transcripts for the one-time native history cutover."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, exists, literal, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import aliased

from db.models import Conversation, Message


class TranscriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptTurn:
    message_id: str
    user_input: str
    response: str | None


@dataclass(frozen=True)
class LeafTranscript:
    conversation_id: str
    leaf_message_id: str
    title: str
    turns: tuple[TranscriptTurn, ...]


class TranscriptReader:
    """Load one root-to-leaf display transcript without interpreting old events."""

    def __init__(self, engine: AsyncEngine):
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def load(
        self,
        *,
        conversation_id: str,
        leaf_message_id: str,
        expected_path_message_count: int,
    ) -> LeafTranscript:
        leaf = aliased(Message)
        path = (
            select(
                leaf.id.label("message_id"),
                leaf.parent_id.label("parent_id"),
                leaf.user_input.label("user_input"),
                leaf.response.label("response"),
                literal(0).label("distance"),
            )
            .where(
                leaf.id == leaf_message_id,
                leaf.conversation_id == conversation_id,
            )
            .cte("message_path", recursive=True)
        )
        parent = aliased(Message)
        path = path.union_all(
            select(
                parent.id,
                parent.parent_id,
                parent.user_input,
                parent.response,
                path.c.distance + 1,
            ).join(
                path,
                and_(
                    parent.id == path.c.parent_id,
                    parent.conversation_id == conversation_id,
                ),
            )
        )

        async with self._sessions() as session:
            conversation = (
                await session.execute(
                    select(Conversation.id, Conversation.title).where(
                        Conversation.id == conversation_id
                    )
                )
            ).one_or_none()
            if conversation is None:
                raise TranscriptError(f"conversation {conversation_id!r} not found")

            rows = (
                await session.execute(
                    select(
                        path.c.message_id,
                        path.c.user_input,
                        path.c.response,
                        path.c.distance,
                    ).order_by(path.c.distance.desc())
                )
            ).all()
            if not rows:
                raise TranscriptError(
                    f"leaf {leaf_message_id!r} not found in conversation {conversation_id!r}"
                )
            if len(rows) != expected_path_message_count:
                raise TranscriptError(
                    f"leaf {leaf_message_id!r} path changed after scan: "
                    f"expected {expected_path_message_count}, found {len(rows)}"
                )
            has_child = await session.scalar(
                select(exists().where(
                    Message.conversation_id == conversation_id,
                    Message.parent_id == leaf_message_id,
                ))
            )
            if has_child:
                raise TranscriptError(
                    f"message {leaf_message_id!r} is no longer a leaf; backend writers "
                    "must remain stopped for the whole migration"
                )

        return LeafTranscript(
            conversation_id=conversation_id,
            leaf_message_id=leaf_message_id,
            title=str(conversation.title or ""),
            turns=tuple(
                TranscriptTurn(
                    message_id=row.message_id,
                    user_input=row.user_input,
                    response=row.response,
                )
                for row in rows
            ),
        )


def _truncate_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    marker = f"\n...[truncated to {limit} characters]...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return f"{text[:head]}{marker}{text[-tail:] if tail else ''}"


def _render_turn(turn: TranscriptTurn, index: int, *, field_max_chars: int) -> str:
    user = _truncate_text(turn.user_input, field_max_chars)
    assistant = (
        _truncate_text(turn.response, field_max_chars)
        if turn.response is not None and turn.response != ""
        else "[No completed assistant response was stored for this turn.]"
    )
    return (
        f"Turn {index}\n"
        f"User:\n{user}\n\n"
        f"Assistant:\n{assistant}"
    )


def render_bounded_transcript(
    transcript: LeafTranscript,
    *,
    max_chars: int,
    recent_turns: int,
    field_max_chars: int,
) -> str:
    """Keep the first turn and as many newest complete display turns as fit."""
    if max_chars < 2_000:
        raise ValueError("max_chars must be at least 2000")
    if recent_turns < 1:
        raise ValueError("recent_turns must be at least 1")
    if field_max_chars < 100:
        raise ValueError("field_max_chars must be at least 100")
    if not transcript.turns:
        raise TranscriptError(f"leaf {transcript.leaf_message_id!r} has no turns")

    title = _truncate_text(transcript.title or "(untitled)", min(1_000, field_max_chars))
    header = (
        "Display-only conversation transcript used for protocol migration. "
        "Tool execution details are intentionally omitted.\n\n"
        f"Conversation title: {title}\n"
        f"Leaf message: {transcript.leaf_message_id}\n"
        f"Total turns on path: {len(transcript.turns)}"
    )
    blocks = [
        _render_turn(turn, index, field_max_chars=field_max_chars)
        for index, turn in enumerate(transcript.turns, start=1)
    ]
    omission = "[Earlier display turns omitted by the migration size limit.]"
    separator = "\n\n---\n\n"

    def rendered_length(indices: set[int]) -> int:
        part_lengths = [len(header), *(len(blocks[index]) for index in indices)]
        if len(indices) < len(blocks):
            part_lengths.append(len(omission))
        return sum(part_lengths) + len(separator) * (len(part_lengths) - 1)

    selected = {0}
    newest_candidates = list(
        range(max(1, len(blocks) - recent_turns), len(blocks))
    )
    # The defaults guarantee the first and newest bounded blocks fit. Validate
    # custom values rather than silently dropping the most recent turn.
    minimum_selection = {0}
    if len(blocks) > 1:
        minimum_selection.add(len(blocks) - 1)
    minimum = rendered_length(minimum_selection)
    if minimum > max_chars:
        raise ValueError(
            "max_chars is too small for the first and newest bounded turns; "
            "raise max_chars or lower field_max_chars"
        )

    for index in reversed(newest_candidates):
        if index in selected:
            continue
        candidate = selected | {index}
        if rendered_length(candidate) > max_chars:
            continue
        selected = candidate

    ordered = sorted(selected)
    parts = [header, blocks[0]]
    if len(selected) < len(blocks):
        parts.append(omission)
    parts.extend(blocks[index] for index in ordered if index != 0)
    return separator.join(parts)


def build_mechanical_summary(
    transcript: LeafTranscript,
    *,
    max_chars: int,
    recent_turns: int,
    field_max_chars: int,
) -> str:
    return render_bounded_transcript(
        transcript,
        max_chars=max_chars,
        recent_turns=recent_turns,
        field_max_chars=field_max_chars,
    )


def build_semantic_messages(
    transcript: LeafTranscript,
    *,
    system_prompt: str,
    max_chars: int,
    recent_turns: int,
    field_max_chars: int,
) -> list[dict[str, str]]:
    display_transcript = render_bounded_transcript(
        transcript,
        max_chars=max_chars,
        recent_turns=recent_turns,
        field_max_chars=field_max_chars,
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": display_transcript},
        {
            "role": "user",
            "content": (
                "Produce the migration summary now. Use only facts present in the "
                "display transcript, do not invent omitted tool interactions, and "
                "respond with plain text only."
            ),
        },
    ]

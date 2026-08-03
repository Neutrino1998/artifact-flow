import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from native_tool_history.boundaries import (
    BoundaryError,
    COMPACTION_START,
    COMPACTION_SUMMARY,
    SUMMARY_FRAME,
    apply_boundaries,
    assert_source_database,
    source_database_fingerprint,
    verify_boundaries,
)
from native_tool_history.checkpoint import Checkpoint, CheckpointError
from native_tool_history.manifest import (
    ManifestError,
    SourceConversation,
    SourceMessage,
    build_manifest,
    scan_database,
)
from native_tool_history.transcript import (
    TranscriptReader,
    build_mechanical_summary,
    build_semantic_messages,
)
from scripts.native_tool_history_migration import (
    _assert_separate_checkpoint,
    _generate,
    _generate_semantic,
)
from core.event_history import build_event_history
from core.events import ExecutionEvent
from db.models import Base, Conversation, Message, MessageEvent


async def _source_database(tmp_path: Path) -> str:
    path = tmp_path / "source.db"
    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        session.add_all([
            Conversation(id="conv-1", active_branch="leaf-active", title="tree"),
            Conversation(id="conv-empty", active_branch=None, title="empty"),
            Message(
                id="root",
                conversation_id="conv-1",
                parent_id=None,
                user_input="root",
                response="root response",
            ),
            Message(
                id="middle",
                conversation_id="conv-1",
                parent_id="root",
                user_input="middle",
                response="middle response",
            ),
            Message(
                id="leaf-active",
                conversation_id="conv-1",
                parent_id="middle",
                user_input="active",
                response="active response",
            ),
            Message(
                id="leaf-other",
                conversation_id="conv-1",
                parent_id="root",
                user_input="other",
                response="*Task cancelled by user*",
            ),
        ])
        await session.flush()
        session.add_all([
            MessageEvent(
                message_id="root",
                event_type="agent_start",
                agent_name="lead_agent",
                data={},
            ),
            MessageEvent(
                message_id="root",
                event_type="subagent_instruction",
                agent_name="research_agent",
                data={"instruction": "research"},
            ),
            MessageEvent(
                message_id="middle",
                event_type="subagent_instruction",
                agent_name="explore_agent",
                data={"instruction": "explore"},
            ),
        ])
        await session.commit()
    await engine.dispose()
    return url


def _create_scan(
    checkpoint: Checkpoint,
    database_url: str,
    scan,
) -> None:
    checkpoint.create_scan(
        "migration-1",
        "sqlite",
        source_database_fingerprint(database_url),
        scan,
    )


@pytest.mark.asyncio
async def test_stopped_scan_enumerates_leaf_lead_candidate_kinds(tmp_path):
    scan = await scan_database(await _source_database(tmp_path))

    assert scan.conversations == 2
    assert scan.messages == 4
    assert scan.empty_conversations == 1
    assert len(scan.leaves) == 2
    by_leaf = {leaf.leaf_message_id: leaf for leaf in scan.leaves}
    assert by_leaf["leaf-active"].is_active_branch is True
    assert by_leaf["leaf-active"].path_message_count == 3

    task_keys = {
        (task.leaf_message_id, task.summary_kind)
        for task in scan.tasks
    }
    assert task_keys == {
        ("leaf-active", "mechanical"),
        ("leaf-active", "semantic"),
        ("leaf-other", "mechanical"),
    }


@pytest.mark.asyncio
async def test_checkpoint_keeps_semantic_and_mechanical_as_distinct_resume_tasks(tmp_path):
    database_url = await _source_database(tmp_path)
    scan = await scan_database(database_url)
    checkpoint = Checkpoint(tmp_path / "checkpoint.sqlite")
    _create_scan(checkpoint, database_url, scan)

    report = checkpoint.report("migration-1")

    assert report["leaves"] == {
        "total": 2,
        "active": 1,
        "max_path_messages": 3,
    }
    assert report["tasks"]["mechanical"] == {"pending": 2}
    assert report["tasks"]["semantic"] == {"pending": 1}
    assert report["observations"] == {"failed_tasks": 0}
    assert report["blocking"] == {
        "missing_required_task_rows": 0,
        "exhausted_leaf_boundaries": 0,
        "unfinished_tasks": 3,
    }
    assert report["ready_for_apply"] is False

    checkpoint.set_task_result(
        migration_id="migration-1",
        conversation_id="conv-1",
        leaf_message_id="leaf-active",
        summary_kind="semantic",
        status="failed",
        attempts=2,
        error="provider unavailable",
    )
    failed_report = checkpoint.report("migration-1")
    assert failed_report["observations"]["failed_tasks"] == 1
    # Semantic failure alone is non-blocking: the mechanical task is its
    # deterministic fallback and remains independently resumable.
    assert failed_report["blocking"] == {
        "missing_required_task_rows": 0,
        "exhausted_leaf_boundaries": 0,
        "unfinished_tasks": 2,
    }

    for task in scan.tasks:
        if task.summary_kind == "semantic":
            continue
        checkpoint.set_task_result(
            migration_id="migration-1",
            conversation_id=task.conversation_id,
            leaf_message_id=task.leaf_message_id,
            summary_kind=task.summary_kind,
            status="succeeded",
            attempts=1,
            summary_content=f"{task.summary_kind} boundary",
        )
    ready_report = checkpoint.report("migration-1")
    assert ready_report["observations"]["failed_tasks"] == 1
    assert not any(ready_report["blocking"].values())
    assert ready_report["ready_for_apply"] is True

    with pytest.raises(CheckpointError, match="already exists"):
        _create_scan(checkpoint, database_url, scan)

    with pytest.raises(CheckpointError, match="requires non-blank summary_content"):
        checkpoint.set_task_result(
            migration_id="migration-1",
            conversation_id="conv-1",
            leaf_message_id="leaf-active",
            summary_kind="mechanical",
            status="succeeded",
            attempts=1,
        )


@pytest.mark.asyncio
async def test_report_detects_missing_required_lead_task_row(tmp_path):
    database_url = await _source_database(tmp_path)
    scan = await scan_database(database_url)
    path = tmp_path / "checkpoint.sqlite"
    checkpoint = Checkpoint(path)
    _create_scan(checkpoint, database_url, scan)

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            DELETE FROM summary_tasks
            WHERE migration_id = 'migration-1'
              AND leaf_message_id = 'leaf-other'
              AND summary_kind = 'mechanical'
            """
        )

    report = checkpoint.report("migration-1")

    assert report["blocking"]["missing_required_task_rows"] == 1
    assert report["ready_for_apply"] is False


@pytest.mark.asyncio
async def test_report_blocks_while_optional_candidate_is_unfinished(tmp_path):
    database_url = await _source_database(tmp_path)
    scan = await scan_database(database_url)
    checkpoint = Checkpoint(tmp_path / "checkpoint.sqlite")
    _create_scan(checkpoint, database_url, scan)

    for task in scan.tasks:
        if task.summary_kind == "semantic":
            continue
        checkpoint.set_task_result(
            migration_id="migration-1",
            conversation_id=task.conversation_id,
            leaf_message_id=task.leaf_message_id,
            summary_kind=task.summary_kind,
            status="succeeded",
            attempts=1,
            summary_content="mechanical boundary",
        )

    report = checkpoint.report("migration-1")

    assert report["blocking"]["unfinished_tasks"] == 1
    assert report["blocking"]["exhausted_leaf_boundaries"] == 0
    assert report["ready_for_apply"] is False


def test_manifest_rejects_non_leaf_active_branch():
    conversations = [SourceConversation("conv", "root")]
    messages = [
        SourceMessage("root", "conv", None),
        SourceMessage("leaf", "conv", "root"),
    ]

    with pytest.raises(ManifestError, match="is not a leaf"):
        build_manifest(conversations, messages)


def test_manifest_rejects_parent_cycle_even_if_other_component_has_a_leaf():
    conversations = [SourceConversation("conv", "leaf")]
    messages = [
        SourceMessage("root", "conv", None),
        SourceMessage("leaf", "conv", "root"),
        SourceMessage("cycle-a", "conv", "cycle-b"),
        SourceMessage("cycle-b", "conv", "cycle-a"),
    ]

    with pytest.raises(ManifestError, match="parent cycle"):
        build_manifest(conversations, messages)


def test_manifest_resolves_long_linear_history_without_path_copies():
    count = 5_000
    messages = [
        SourceMessage(
            f"message-{index}",
            "conv",
            None if index == 0 else f"message-{index - 1}",
        )
        for index in range(count)
    ]

    scan = build_manifest(
        [SourceConversation("conv", f"message-{count - 1}")],
        messages,
    )

    assert len(scan.leaves) == 1
    assert scan.leaves[0].path_message_count == count
    assert {task.summary_kind for task in scan.tasks} == {
        "mechanical",
        "semantic",
    }


@pytest.mark.parametrize(
    ("conversations", "messages", "error"),
    [
        (
            [SourceConversation("conv", "missing")],
            [SourceMessage("leaf", "conv", None)],
            "dangling active_branch",
        ),
        (
            [SourceConversation("conv", "leaf")],
            [SourceMessage("leaf", "conv", "missing-parent")],
            "references missing parent",
        ),
        (
            [],
            [SourceMessage("leaf", "missing-conversation", None)],
            "references missing conversation",
        ),
    ],
)
def test_manifest_rejects_invalid_stopped_source(
    conversations, messages, error
):
    with pytest.raises(ManifestError, match=error):
        build_manifest(conversations, messages)


def test_checkpoint_must_not_be_source_sqlite(tmp_path):
    source = tmp_path / "source.db"
    url = f"sqlite+aiosqlite:///{source}"

    with pytest.raises(RuntimeError, match="must not be the source"):
        _assert_separate_checkpoint(source, url)

    _assert_separate_checkpoint(tmp_path / "checkpoint.sqlite", url)


@pytest.mark.asyncio
async def test_checkpoint_is_bound_to_the_scanned_database_target(tmp_path):
    database_url = await _source_database(tmp_path)
    scan = await scan_database(database_url)
    checkpoint = Checkpoint(tmp_path / "checkpoint.sqlite")
    _create_scan(checkpoint, database_url, scan)

    assert_source_database(checkpoint, "migration-1", database_url)
    other_url = f"sqlite+aiosqlite:///{tmp_path / 'other.db'}"
    with pytest.raises(BoundaryError, match="different source database target"):
        assert_source_database(checkpoint, "migration-1", other_url)


def test_database_target_fingerprint_excludes_postgres_credentials():
    first = source_database_fingerprint(
        "postgresql+asyncpg://operator:first@db.internal/artifactflow"
    )
    second = source_database_fingerprint(
        "postgresql+asyncpg://other:second@db.internal:5432/artifactflow"
    )

    assert first == second
    assert first != source_database_fingerprint(
        "postgresql+asyncpg://operator:first@db.internal/other"
    )


def test_checkpoint_rejects_obsolete_schema(tmp_path):
    path = tmp_path / "checkpoint.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=1")

    with pytest.raises(CheckpointError, match="unsupported checkpoint schema version 1"):
        Checkpoint(path).initialize()


def test_offline_boundary_event_names_match_runtime_contract():
    from core.events import StreamEventType

    assert COMPACTION_START == StreamEventType.COMPACTION_START.value
    assert COMPACTION_SUMMARY == StreamEventType.COMPACTION_SUMMARY.value


@pytest.mark.asyncio
async def test_transcript_uses_display_fields_and_keeps_cancelled_leaf(tmp_path):
    database_url = await _source_database(tmp_path)
    engine = create_async_engine(database_url)
    try:
        transcript = await TranscriptReader(engine).load(
            conversation_id="conv-1",
            leaf_message_id="leaf-other",
            expected_path_message_count=2,
        )
    finally:
        await engine.dispose()

    summary = build_mechanical_summary(
        transcript,
        max_chars=5_000,
        recent_turns=4,
        field_max_chars=500,
    )

    assert [turn.message_id for turn in transcript.turns] == ["root", "leaf-other"]
    assert "*Task cancelled by user*" in summary
    assert "subagent_instruction" not in summary
    assert "Tool execution details are intentionally omitted" in summary


@pytest.mark.asyncio
async def test_transcript_fails_if_leaf_changed_after_scan(tmp_path):
    database_url = await _source_database(tmp_path)
    engine = create_async_engine(database_url)
    async with AsyncSession(engine) as session:
        session.add(Message(
            id="new-child",
            conversation_id="conv-1",
            parent_id="leaf-other",
            user_input="new",
            response="new response",
        ))
        await session.commit()

    try:
        with pytest.raises(RuntimeError, match="no longer a leaf"):
            await TranscriptReader(engine).load(
                conversation_id="conv-1",
                leaf_message_id="leaf-other",
                expected_path_message_count=2,
            )
    finally:
        await engine.dispose()


def test_bounded_transcript_keeps_first_and_latest_complete_turns():
    from native_tool_history.transcript import LeafTranscript, TranscriptTurn

    transcript = LeafTranscript(
        conversation_id="conv",
        leaf_message_id="m4",
        title="topic",
        turns=tuple(
            TranscriptTurn(f"m{i}", f"user-{i}-" + "u" * 250, f"assistant-{i}-" + "a" * 250)
            for i in range(1, 5)
        ),
    )

    summary = build_mechanical_summary(
        transcript,
        max_chars=2_000,
        recent_turns=3,
        field_max_chars=220,
    )
    semantic = build_semantic_messages(
        transcript,
        system_prompt="compact",
        max_chars=2_000,
        recent_turns=3,
        field_max_chars=220,
    )

    assert len(summary) <= 2_000
    assert "user-1" in summary
    assert "user-4" in summary
    assert "omitted" in summary
    assert semantic[0] == {"role": "system", "content": "compact"}
    assert "user-4" in semantic[1]["content"]


@pytest.mark.asyncio
async def test_semantic_summary_uses_complete_final_after_stream_retry(monkeypatch):
    async def fake_stream(*args, **kwargs):
        yield {"type": "content", "content": "partial-attempt\n"}
        yield {
            "type": "final",
            "content": "complete-retry",
            "tool_calls": [],
        }

    monkeypatch.setattr("models.llm.astream_with_retry", fake_stream)

    result = await _generate_semantic(
        [{"role": "user", "content": "summarize"}],
        model="compact",
        max_retries=2,
    )

    assert result == "complete-retry"


@pytest.mark.asyncio
async def test_generate_skip_semantic_builds_mechanical_fallbacks(
    tmp_path, monkeypatch, capsys
):
    database_url = await _source_database(tmp_path)
    scan = await scan_database(database_url)
    checkpoint_path = tmp_path / "checkpoint.sqlite"
    checkpoint = Checkpoint(checkpoint_path)
    _create_scan(checkpoint, database_url, scan)
    monkeypatch.setenv("ARTIFACTFLOW_DATABASE_URL", database_url)
    monkeypatch.delenv("ARTIFACTFLOW_DATABASE_URLS", raising=False)

    args = Namespace(
        checkpoint=checkpoint_path,
        migration_id="migration-1",
        resume=False,
        retry_failed=False,
        skip_semantic=True,
        semantic_model=None,
        concurrency=2,
        max_retries=1,
        mechanical_max_chars=5_000,
        mechanical_recent_turns=8,
        mechanical_field_max_chars=500,
        semantic_input_max_chars=5_000,
        semantic_recent_turns=8,
        semantic_field_max_chars=500,
    )

    assert await _generate(args) == 0
    report = checkpoint.report("migration-1")
    assert report["status"] == "ready"
    assert report["tasks"]["mechanical"] == {"succeeded": 2}
    assert report["tasks"]["semantic"] == {"failed": 1}
    assert checkpoint.selected_boundaries("migration-1")[0].summary_kind == "mechanical"
    stderr = capsys.readouterr().err
    assert "WARNING generate task failed" in stderr
    assert "leaf='leaf-active' kind='semantic'" in stderr


async def _ready_checkpoint(tmp_path: Path) -> tuple[str, Checkpoint]:
    database_url = await _source_database(tmp_path)
    scan = await scan_database(database_url)
    checkpoint = Checkpoint(tmp_path / "checkpoint.sqlite")
    _create_scan(checkpoint, database_url, scan)
    for task in checkpoint.list_tasks("migration-1"):
        if task.summary_kind == "semantic":
            checkpoint.set_task_result(
                migration_id=task.migration_id,
                conversation_id=task.conversation_id,
                leaf_message_id=task.leaf_message_id,
                summary_kind=task.summary_kind,
                status="failed",
                attempts=1,
                error="semantic unavailable",
            )
        else:
            checkpoint.set_task_result(
                migration_id=task.migration_id,
                conversation_id=task.conversation_id,
                leaf_message_id=task.leaf_message_id,
                summary_kind=task.summary_kind,
                status="succeeded",
                attempts=1,
                summary_content=f"summary for {task.leaf_message_id}",
            )
    checkpoint.set_run_status("migration-1", "ready")
    return database_url, checkpoint


@pytest.mark.asyncio
async def test_apply_is_transactional_idempotent_and_forms_history_boundary(tmp_path):
    database_url, checkpoint = await _ready_checkpoint(tmp_path)
    engine = create_async_engine(database_url)
    try:
        first = await apply_boundaries(engine, checkpoint, "migration-1")
        second = await apply_boundaries(engine, checkpoint, "migration-1")
        verified = await verify_boundaries(engine, checkpoint, "migration-1")
        async with AsyncSession(engine) as session:
            rows = list((await session.execute(
                select(MessageEvent)
                .where(MessageEvent.message_id == "leaf-other")
                .order_by(MessageEvent.id)
            )).scalars())
    finally:
        await engine.dispose()

    assert first.inserted == 2
    assert first.already_present == 0
    assert second.inserted == 0
    assert second.already_present == 2
    assert verified.verified == 2
    assert len(rows) == 2
    events = [
        ExecutionEvent(
            event_type="llm_complete",
            agent_name="lead_agent",
            data={"content": "legacy <tool_call>xml</tool_call>"},
            is_historical=True,
        ),
        *[
            ExecutionEvent(
                event_type=row.event_type,
                agent_name=row.agent_name,
                data=row.data,
                event_id=row.event_id,
                is_historical=True,
            )
            for row in rows
        ],
    ]
    history = build_event_history(events, "lead_agent")
    assert history == [{
        "role": "user",
        "content": f"{SUMMARY_FRAME}\n\nsummary for leaf-other",
    }]


@pytest.mark.asyncio
async def test_apply_rejects_incomplete_existing_pair(tmp_path):
    database_url, checkpoint = await _ready_checkpoint(tmp_path)
    engine = create_async_engine(database_url)
    try:
        await apply_boundaries(engine, checkpoint, "migration-1")
        async with AsyncSession(engine) as session:
            await session.execute(
                delete(MessageEvent).where(
                    MessageEvent.message_id == "leaf-other",
                    MessageEvent.event_type == "compaction_summary",
                )
            )
            await session.commit()
        with pytest.raises(BoundaryError, match="incomplete"):
            await apply_boundaries(engine, checkpoint, "migration-1")
    finally:
        await engine.dispose()

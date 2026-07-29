import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from native_tool_history.checkpoint import Checkpoint, CheckpointError
from native_tool_history.manifest import (
    ManifestError,
    SourceConversation,
    SourceMessage,
    build_manifest,
    scan_database,
)
from scripts.native_tool_history_migration import _assert_separate_checkpoint
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
                response="other response",
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


@pytest.mark.asyncio
async def test_stopped_scan_enumerates_leaf_agents_and_candidate_kinds(tmp_path):
    scan = await scan_database(await _source_database(tmp_path))

    assert scan.conversations == 2
    assert scan.messages == 4
    assert scan.empty_conversations == 1
    assert len(scan.leaves) == 2
    by_leaf = {leaf.leaf_message_id: leaf for leaf in scan.leaves}
    assert by_leaf["leaf-active"].is_active_branch is True
    assert by_leaf["leaf-active"].path_message_count == 3
    assert by_leaf["leaf-active"].agents == (
        "lead_agent",
        "explore_agent",
        "research_agent",
    )
    assert by_leaf["leaf-other"].agents == ("lead_agent", "research_agent")

    task_keys = {
        (task.leaf_message_id, task.agent_name, task.summary_kind)
        for task in scan.tasks
    }
    assert ("leaf-active", "lead_agent", "mechanical") in task_keys
    assert ("leaf-active", "lead_agent", "semantic") in task_keys
    assert ("leaf-other", "lead_agent", "mechanical") in task_keys
    assert ("leaf-other", "lead_agent", "semantic") not in task_keys
    assert ("leaf-active", "explore_agent", "reset") in task_keys
    assert ("leaf-active", "research_agent", "reset") in task_keys
    assert ("leaf-other", "research_agent", "reset") in task_keys


@pytest.mark.asyncio
async def test_checkpoint_keeps_semantic_and_mechanical_as_distinct_resume_tasks(tmp_path):
    scan = await scan_database(await _source_database(tmp_path))
    checkpoint = Checkpoint(tmp_path / "checkpoint.sqlite")
    checkpoint.create_scan("migration-1", "sqlite", scan)

    report = checkpoint.report("migration-1")

    assert report["leaves"] == {
        "total": 2,
        "active": 1,
        "max_path_messages": 3,
        "max_agents": 3,
    }
    assert report["tasks"]["mechanical"] == {"pending": 2}
    assert report["tasks"]["semantic"] == {"pending": 1}
    assert report["tasks"]["reset"] == {"pending": 3}
    assert report["observations"] == {"failed_tasks": 0}
    assert report["blocking"] == {
        "missing_required_task_rows": 0,
        "exhausted_leaf_boundaries": 0,
        "failed_reset_tasks": 0,
    }
    assert report["ready_for_apply"] is False

    checkpoint.set_task_result(
        migration_id="migration-1",
        conversation_id="conv-1",
        leaf_message_id="leaf-active",
        agent_name="lead_agent",
        summary_kind="semantic",
        status="failed",
        attempts=2,
        error="provider unavailable",
    )
    failed_report = checkpoint.report("migration-1")
    assert failed_report["observations"]["failed_tasks"] == 1
    # Semantic failure alone is non-blocking: the mechanical task is its
    # deterministic fallback and remains independently resumable.
    assert not any(failed_report["blocking"].values())

    for task in scan.tasks:
        if task.summary_kind == "semantic":
            continue
        checkpoint.set_task_result(
            migration_id="migration-1",
            conversation_id=task.conversation_id,
            leaf_message_id=task.leaf_message_id,
            agent_name=task.agent_name,
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
        checkpoint.create_scan("migration-1", "sqlite", scan)

    with pytest.raises(CheckpointError, match="requires non-blank summary_content"):
        checkpoint.set_task_result(
            migration_id="migration-1",
            conversation_id="conv-1",
            leaf_message_id="leaf-active",
            agent_name="lead_agent",
            summary_kind="mechanical",
            status="succeeded",
            attempts=1,
        )


@pytest.mark.asyncio
async def test_report_detects_missing_reset_task_row(tmp_path):
    scan = await scan_database(await _source_database(tmp_path))
    path = tmp_path / "checkpoint.sqlite"
    checkpoint = Checkpoint(path)
    checkpoint.create_scan("migration-1", "sqlite", scan)

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            DELETE FROM summary_tasks
            WHERE migration_id = 'migration-1'
              AND leaf_message_id = 'leaf-active'
              AND agent_name = 'explore_agent'
              AND summary_kind = 'reset'
            """
        )

    report = checkpoint.report("migration-1")

    assert report["blocking"]["missing_required_task_rows"] == 1
    assert report["ready_for_apply"] is False


def test_manifest_rejects_non_leaf_active_branch():
    conversations = [SourceConversation("conv", "root")]
    messages = [
        SourceMessage("root", "conv", None),
        SourceMessage("leaf", "conv", "root"),
    ]

    with pytest.raises(ManifestError, match="is not a leaf"):
        build_manifest(conversations, messages, [])


def test_manifest_rejects_parent_cycle_even_if_other_component_has_a_leaf():
    conversations = [SourceConversation("conv", "leaf")]
    messages = [
        SourceMessage("root", "conv", None),
        SourceMessage("leaf", "conv", "root"),
        SourceMessage("cycle-a", "conv", "cycle-b"),
        SourceMessage("cycle-b", "conv", "cycle-a"),
    ]

    with pytest.raises(ManifestError, match="parent cycle"):
        build_manifest(conversations, messages, [])


@pytest.mark.parametrize(
    ("conversations", "messages", "event_agents", "error"),
    [
        (
            [SourceConversation("conv", "missing")],
            [SourceMessage("leaf", "conv", None)],
            [],
            "dangling active_branch",
        ),
        (
            [SourceConversation("conv", "leaf")],
            [SourceMessage("leaf", "conv", "missing-parent")],
            [],
            "references missing parent",
        ),
        (
            [SourceConversation("conv", "leaf")],
            [SourceMessage("leaf", "conv", None)],
            [("missing-message", "research_agent")],
            "event agent link references missing message",
        ),
    ],
)
def test_manifest_rejects_invalid_stopped_source(
    conversations, messages, event_agents, error
):
    with pytest.raises(ManifestError, match=error):
        build_manifest(conversations, messages, event_agents)


def test_checkpoint_must_not_be_source_sqlite(tmp_path):
    source = tmp_path / "source.db"
    url = f"sqlite+aiosqlite:///{source}"

    with pytest.raises(RuntimeError, match="must not be the source"):
        _assert_separate_checkpoint(source, url)

    _assert_separate_checkpoint(tmp_path / "checkpoint.sqlite", url)

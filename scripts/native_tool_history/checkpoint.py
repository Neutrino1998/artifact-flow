"""Separate SQLite checkpoint for the fully stopped history migration."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.time import utc_now

from .manifest import ScanResult


SCHEMA_VERSION = 2
TASK_STATUSES = frozenset({"pending", "running", "succeeded", "failed"})
RUN_STATUSES = frozenset({"scanned", "generating", "ready", "applied"})


class CheckpointError(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckpointTask:
    migration_id: str
    conversation_id: str
    leaf_message_id: str
    summary_kind: str
    status: str
    attempts: int
    summary_content: str | None
    error: str | None


@dataclass(frozen=True)
class SelectedBoundary:
    migration_id: str
    conversation_id: str
    leaf_message_id: str
    is_active_branch: bool
    path_message_count: int
    summary_kind: str
    summary_content: str


class Checkpoint:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise CheckpointError(
                    f"unsupported checkpoint schema version {version}; "
                    f"expected {SCHEMA_VERSION}"
                )
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS migration_runs (
                    migration_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_database_kind TEXT NOT NULL,
                    conversations INTEGER NOT NULL,
                    messages INTEGER NOT NULL,
                    empty_conversations INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('scanned', 'generating', 'ready', 'applied')
                    )
                );

                CREATE TABLE IF NOT EXISTS manifest_leaves (
                    migration_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    leaf_message_id TEXT NOT NULL,
                    is_active_branch INTEGER NOT NULL CHECK (is_active_branch IN (0, 1)),
                    path_message_count INTEGER NOT NULL CHECK (path_message_count > 0),
                    PRIMARY KEY (migration_id, conversation_id, leaf_message_id),
                    FOREIGN KEY (migration_id) REFERENCES migration_runs(migration_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS summary_tasks (
                    migration_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    leaf_message_id TEXT NOT NULL,
                    summary_kind TEXT NOT NULL CHECK (
                        summary_kind IN ('semantic', 'mechanical')
                    ),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (
                        status IN ('pending', 'running', 'succeeded', 'failed')
                    ),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    summary_content TEXT,
                    error TEXT,
                    CHECK (
                        (status = 'succeeded'
                            AND summary_content IS NOT NULL
                            AND length(trim(summary_content)) > 0
                            AND error IS NULL)
                        OR (status = 'failed'
                            AND summary_content IS NULL
                            AND error IS NOT NULL
                            AND length(trim(error)) > 0)
                        OR (status IN ('pending', 'running')
                            AND summary_content IS NULL AND error IS NULL)
                    ),
                    PRIMARY KEY (
                        migration_id, conversation_id, leaf_message_id,
                        summary_kind
                    ),
                    FOREIGN KEY (
                        migration_id, conversation_id, leaf_message_id
                    ) REFERENCES manifest_leaves(
                        migration_id, conversation_id, leaf_message_id
                    ) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_summary_tasks_status
                    ON summary_tasks(migration_id, status, summary_kind);
            """)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def run_exists(self, migration_id: str) -> bool:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM migration_runs WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            return row is not None

    def get_run_status(self, migration_id: str) -> str:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM migration_runs WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
        if row is None:
            raise CheckpointError(f"migration {migration_id!r} not found")
        return str(row["status"])

    def set_run_status(self, migration_id: str, status: str) -> None:
        if status not in RUN_STATUSES:
            raise CheckpointError(f"invalid migration status: {status!r}")
        self.initialize()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE migration_runs SET status = ? WHERE migration_id = ?",
                (status, migration_id),
            )
            if cursor.rowcount != 1:
                raise CheckpointError(f"migration {migration_id!r} not found")

    def create_scan(
        self,
        migration_id: str,
        source_database_kind: str,
        scan: ScanResult,
    ) -> None:
        self.initialize()
        if not migration_id.strip():
            raise CheckpointError("migration_id must not be blank")
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO migration_runs (
                        migration_id, created_at, source_database_kind,
                        conversations, messages, empty_conversations, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'scanned')
                    """,
                    (
                        migration_id,
                        utc_now().isoformat(),
                        source_database_kind,
                        scan.conversations,
                        scan.messages,
                        scan.empty_conversations,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO manifest_leaves (
                        migration_id, conversation_id, leaf_message_id,
                        is_active_branch, path_message_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            migration_id,
                            leaf.conversation_id,
                            leaf.leaf_message_id,
                            int(leaf.is_active_branch),
                            leaf.path_message_count,
                        )
                        for leaf in scan.leaves
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO summary_tasks (
                        migration_id, conversation_id, leaf_message_id,
                        summary_kind
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            migration_id,
                            task.conversation_id,
                            task.leaf_message_id,
                            task.summary_kind,
                        )
                        for task in scan.tasks
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise CheckpointError(
                f"migration {migration_id!r} already exists or scan rows conflict; "
                "use --resume to inspect the existing checkpoint"
            ) from exc

    def set_task_result(
        self,
        *,
        migration_id: str,
        conversation_id: str,
        leaf_message_id: str,
        summary_kind: str,
        status: str,
        attempts: int,
        summary_content: str | None = None,
        error: str | None = None,
    ) -> None:
        """Stage-5 seam: update one stable task without changing its identity."""
        if status not in TASK_STATUSES:
            raise CheckpointError(f"invalid task status: {status!r}")
        if status == "succeeded":
            if not summary_content or not summary_content.strip() or error is not None:
                raise CheckpointError(
                    "succeeded task requires non-blank summary_content and no error"
                )
        elif status == "failed":
            if summary_content is not None or not error or not error.strip():
                raise CheckpointError(
                    "failed task requires a non-blank error and no summary_content"
                )
        elif summary_content is not None or error is not None:
            raise CheckpointError(
                f"{status} task cannot contain summary_content or error"
            )
        self.initialize()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE summary_tasks
                SET status = ?, attempts = ?, summary_content = ?, error = ?
                WHERE migration_id = ? AND conversation_id = ?
                  AND leaf_message_id = ? AND summary_kind = ?
                """,
                (
                    status,
                    attempts,
                    summary_content,
                    error,
                    migration_id,
                    conversation_id,
                    leaf_message_id,
                    summary_kind,
                ),
            )
            if cursor.rowcount != 1:
                raise CheckpointError("summary task not found")

    def list_tasks(
        self,
        migration_id: str,
        *,
        statuses: set[str] | frozenset[str] | None = None,
    ) -> list[CheckpointTask]:
        self.initialize()
        if statuses is not None:
            unknown = set(statuses) - TASK_STATUSES
            if unknown:
                raise CheckpointError(f"invalid task statuses: {sorted(unknown)!r}")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT migration_id, conversation_id, leaf_message_id,
                       summary_kind, status, attempts, summary_content, error
                FROM summary_tasks
                WHERE migration_id = ?
                ORDER BY conversation_id, leaf_message_id, summary_kind
                """,
                (migration_id,),
            ).fetchall()
        if not self.run_exists(migration_id):
            raise CheckpointError(f"migration {migration_id!r} not found")
        return [
            CheckpointTask(
                migration_id=row["migration_id"],
                conversation_id=row["conversation_id"],
                leaf_message_id=row["leaf_message_id"],
                summary_kind=row["summary_kind"],
                status=row["status"],
                attempts=row["attempts"],
                summary_content=row["summary_content"],
                error=row["error"],
            )
            for row in rows
            if statuses is None or row["status"] in statuses
        ]

    def manifest_path_counts(self, migration_id: str) -> dict[tuple[str, str], int]:
        self.initialize()
        with self._connect() as conn:
            run = conn.execute(
                "SELECT 1 FROM migration_runs WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if run is None:
                raise CheckpointError(f"migration {migration_id!r} not found")
            rows = conn.execute(
                """
                SELECT conversation_id, leaf_message_id, path_message_count
                FROM manifest_leaves WHERE migration_id = ?
                """,
                (migration_id,),
            ).fetchall()
        return {
            (row["conversation_id"], row["leaf_message_id"]): int(
                row["path_message_count"]
            )
            for row in rows
        }

    def claim_task(self, task: CheckpointTask, *, retry_failed: bool = False) -> int | None:
        """Atomically mark one resumable task running and return its new attempt."""
        eligible = {"pending", "running"}
        if retry_failed:
            eligible.add("failed")
        placeholders = ", ".join("?" for _ in eligible)
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT status, attempts FROM summary_tasks
                WHERE migration_id = ? AND conversation_id = ?
                  AND leaf_message_id = ? AND summary_kind = ?
                  AND status IN ({placeholders})
                """,
                (
                    task.migration_id,
                    task.conversation_id,
                    task.leaf_message_id,
                    task.summary_kind,
                    *sorted(eligible),
                ),
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"]) + 1
            cursor = conn.execute(
                f"""
                UPDATE summary_tasks
                SET status = 'running', attempts = ?, summary_content = NULL, error = NULL
                WHERE migration_id = ? AND conversation_id = ?
                  AND leaf_message_id = ? AND summary_kind = ?
                  AND status IN ({placeholders})
                """,
                (
                    attempts,
                    task.migration_id,
                    task.conversation_id,
                    task.leaf_message_id,
                    task.summary_kind,
                    *sorted(eligible),
                ),
            )
            if cursor.rowcount != 1:
                return None
            return attempts

    def selected_boundaries(self, migration_id: str) -> list[SelectedBoundary]:
        """Resolve the deterministic semantic-first/mechanical-fallback choice."""
        self.initialize()
        with self._connect() as conn:
            leaves = conn.execute(
                """
                SELECT conversation_id, leaf_message_id, is_active_branch,
                       path_message_count
                FROM manifest_leaves
                WHERE migration_id = ?
                ORDER BY conversation_id, leaf_message_id
                """,
                (migration_id,),
            ).fetchall()
            tasks = conn.execute(
                """
                SELECT conversation_id, leaf_message_id, summary_kind,
                       status, summary_content
                FROM summary_tasks
                WHERE migration_id = ?
                """,
                (migration_id,),
            ).fetchall()
        if not self.run_exists(migration_id):
            raise CheckpointError(f"migration {migration_id!r} not found")

        by_leaf = {
            (row["conversation_id"], row["leaf_message_id"], row["summary_kind"]): row
            for row in tasks
        }
        selected: list[SelectedBoundary] = []
        for leaf in leaves:
            key = (leaf["conversation_id"], leaf["leaf_message_id"])
            semantic = by_leaf.get((*key, "semantic"))
            mechanical = by_leaf.get((*key, "mechanical"))
            choice = None
            if leaf["is_active_branch"] and semantic is not None:
                if semantic["status"] == "succeeded":
                    choice = semantic
            if choice is None and mechanical is not None:
                if mechanical["status"] == "succeeded":
                    choice = mechanical
            if choice is None:
                raise CheckpointError(
                    "leaf has no successful boundary candidate: "
                    f"conversation={key[0]!r} leaf={key[1]!r}"
                )
            selected.append(SelectedBoundary(
                migration_id=migration_id,
                conversation_id=key[0],
                leaf_message_id=key[1],
                is_active_branch=bool(leaf["is_active_branch"]),
                path_message_count=int(leaf["path_message_count"]),
                summary_kind=choice["summary_kind"],
                summary_content=choice["summary_content"],
            ))
        return selected

    def report(self, migration_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as conn:
            run = conn.execute(
                "SELECT * FROM migration_runs WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if run is None:
                raise CheckpointError(f"migration {migration_id!r} not found")
            leaves = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(is_active_branch) AS active,
                       MAX(path_message_count) AS max_path_messages
                FROM manifest_leaves WHERE migration_id = ?
                """,
                (migration_id,),
            ).fetchone()
            task_rows = conn.execute(
                """
                SELECT summary_kind, status, COUNT(*) AS count
                FROM summary_tasks WHERE migration_id = ?
                GROUP BY summary_kind, status
                """,
                (migration_id,),
            ).fetchall()
            leaf_rows = conn.execute(
                """
                SELECT conversation_id, leaf_message_id, is_active_branch
                FROM manifest_leaves WHERE migration_id = ?
                """,
                (migration_id,),
            ).fetchall()
            all_tasks = conn.execute(
                """
                SELECT conversation_id, leaf_message_id, summary_kind, status
                FROM summary_tasks WHERE migration_id = ?
                """,
                (migration_id,),
            ).fetchall()

        by_kind: dict[str, Counter[str]] = {
            "mechanical": Counter(),
            "semantic": Counter(),
        }
        for row in task_rows:
            by_kind[row["summary_kind"]][row["status"]] = row["count"]
        failed = sum(counter["failed"] for counter in by_kind.values())
        tasks_by_leaf: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for task in all_tasks:
            tasks_by_leaf.setdefault(
                (task["conversation_id"], task["leaf_message_id"]), []
            ).append(task)

        missing_required_task_rows = 0
        exhausted_leaf_boundaries = 0
        for leaf in leaf_rows:
            key = (leaf["conversation_id"], leaf["leaf_message_id"])
            leaf_tasks = tasks_by_leaf.get(key, [])
            lead = {
                task["summary_kind"]: task["status"]
                for task in leaf_tasks
            }
            required_lead_kinds = {"mechanical"}
            if leaf["is_active_branch"]:
                required_lead_kinds.add("semantic")
            missing_required_task_rows += len(required_lead_kinds - lead.keys())

            if leaf["is_active_branch"]:
                candidate_statuses = {
                    lead.get("semantic"), lead.get("mechanical")
                }
                if candidate_statuses <= {None, "failed"}:
                    exhausted_leaf_boundaries += 1
            else:
                mechanical_status = lead.get("mechanical")
                if mechanical_status == "failed":
                    exhausted_leaf_boundaries += 1

        unfinished_tasks = sum(
            counter["pending"] + counter["running"]
            for counter in by_kind.values()
        )
        ready_for_apply = (
            missing_required_task_rows == 0
            and exhausted_leaf_boundaries == 0
            and unfinished_tasks == 0
        )
        return {
            "migration_id": migration_id,
            "created_at": run["created_at"],
            "source_database_kind": run["source_database_kind"],
            "status": run["status"],
            "source": {
                "conversations": run["conversations"],
                "messages": run["messages"],
                "empty_conversations": run["empty_conversations"],
            },
            "leaves": {
                "total": leaves["total"] or 0,
                "active": leaves["active"] or 0,
                "max_path_messages": leaves["max_path_messages"] or 0,
            },
            "tasks": {
                kind: dict(sorted(counter.items()))
                for kind, counter in by_kind.items()
            },
            "observations": {
                "failed_tasks": failed,
            },
            "blocking": {
                "missing_required_task_rows": missing_required_task_rows,
                "exhausted_leaf_boundaries": exhausted_leaf_boundaries,
                "unfinished_tasks": unfinished_tasks,
            },
            "ready_for_apply": ready_for_apply,
        }

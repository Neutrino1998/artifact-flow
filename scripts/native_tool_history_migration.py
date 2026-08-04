#!/usr/bin/env python3
"""Fully stopped native tool-call history migration.

The source PostgreSQL database must have no backend writers from ``scan``
through ``verify``. Other application database backends are intentionally not
supported. ``generate`` stores resumable summary candidates in a separate
SQLite checkpoint. ``apply`` is the only command that writes the application
database; it appends one deterministic, transactional compaction boundary pair
per scanned conversation leaf.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from agents.loader import load_agent
from config import config
from native_tool_history.boundaries import (
    BoundaryError,
    apply_boundaries,
    assert_source_database,
    source_database_fingerprint,
    verify_boundaries,
)
from native_tool_history.checkpoint import (
    Checkpoint,
    CheckpointError,
    CheckpointTask,
)
from native_tool_history.manifest import ManifestError, scan_database
from native_tool_history.transcript import (
    TranscriptError,
    TranscriptReader,
    build_mechanical_summary,
    build_semantic_messages,
)


DEFAULT_MECHANICAL_MAX_CHARS = 20_000
DEFAULT_MECHANICAL_RECENT_TURNS = 8
DEFAULT_MECHANICAL_FIELD_MAX_CHARS = 4_000
DEFAULT_SEMANTIC_INPUT_MAX_CHARS = 60_000
DEFAULT_SEMANTIC_RECENT_TURNS = 20
DEFAULT_SEMANTIC_FIELD_MAX_CHARS = 12_000
MAX_CHECKPOINT_ERROR_CHARS = 4_000


def resolve_database_url() -> str:
    urls = os.getenv("ARTIFACTFLOW_DATABASE_URLS", "")
    if urls:
        first = urls.split(",", 1)[0].strip()
        if first:
            return first
    url = os.getenv("ARTIFACTFLOW_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ARTIFACTFLOW_DATABASE_URL(S) is not configured")
    return url


def _require_postgresql_source(database_url: str) -> None:
    backend = make_url(database_url).get_backend_name()
    if not backend.startswith("postgres"):
        raise RuntimeError(
            "native history migration supports PostgreSQL source databases only"
        )


def _print_report(report: dict[str, Any]) -> None:
    source = report["source"]
    leaves = report["leaves"]
    print(f"migration_id={report['migration_id']} status={report['status']}")
    print(
        "source: "
        f"conversations={source['conversations']} messages={source['messages']} "
        f"empty_conversations={source['empty_conversations']}"
    )
    print(
        "leaves: "
        f"total={leaves['total']} active={leaves['active']} "
        f"max_path_messages={leaves['max_path_messages']}"
    )
    for kind, counts in report["tasks"].items():
        print(f"tasks.{kind}: {counts}")
    print(f"observations: {report['observations']}")
    print(f"blocking: {report['blocking']}")
    print(f"ready_for_apply={str(report['ready_for_apply']).lower()}")


class ProgressReporter:
    def __init__(self, total: int, *, succeeded: int = 0, failed: int = 0):
        self.total = total
        self.completed = succeeded + failed
        self.succeeded = succeeded
        self.failed = failed
        self.inflight = 0
        self.started = time.monotonic()
        self._recent_completions: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def task_started(self) -> None:
        async with self._lock:
            self.inflight += 1

    async def task_finished(self, *, success: bool) -> None:
        async with self._lock:
            self.inflight -= 1
            self.completed += 1
            if success:
                self.succeeded += 1
            else:
                self.failed += 1
            now = time.monotonic()
            self._recent_completions.append(now)
            while self._recent_completions and self._recent_completions[0] < now - 60:
                self._recent_completions.popleft()
            window_start = max(self.started, now - 60)
            elapsed = max(0.001, now - window_start)
            rate = len(self._recent_completions) / elapsed
            remaining = self.total - self.completed
            eta = remaining / rate if rate > 0 else 0.0
            print(
                "generate: "
                f"total={self.total} completed={self.completed} "
                f"succeeded={self.succeeded} failed={self.failed} "
                f"inflight={self.inflight} rate={rate:.2f}/s eta={eta:.1f}s",
                flush=True,
            )


async def _scan(args: argparse.Namespace) -> int:
    database_url = resolve_database_url()
    _require_postgresql_source(database_url)
    checkpoint = Checkpoint(args.checkpoint.resolve())
    if checkpoint.run_exists(args.migration_id):
        if not args.resume:
            raise CheckpointError(
                f"migration {args.migration_id!r} already exists; pass --resume"
            )
        _print_report(checkpoint.report(args.migration_id))
        return 0

    if args.resume:
        raise CheckpointError(
            f"migration {args.migration_id!r} does not exist; remove --resume"
        )
    scan = await scan_database(database_url)
    checkpoint.create_scan(
        args.migration_id,
        make_url(database_url).get_backend_name(),
        source_database_fingerprint(database_url),
        scan,
    )
    _print_report(checkpoint.report(args.migration_id))
    return 0


def _report(args: argparse.Namespace) -> int:
    report = Checkpoint(args.checkpoint.resolve()).report(args.migration_id)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report["ready_for_apply"] else 1


def _checkpoint_error(exc: BaseException) -> str:
    value = f"{type(exc).__name__}: {exc}".strip()
    return value[:MAX_CHECKPOINT_ERROR_CHARS] or type(exc).__name__


def _is_expected_task_failure(exc: Exception) -> bool:
    if isinstance(exc, (RuntimeError, TimeoutError)):
        return True
    # LiteLLM maps provider/network/status failures onto OpenAI's typed error
    # hierarchy. Import it only on a failure path so the dormant CLI stays light.
    from openai import OpenAIError

    return isinstance(exc, OpenAIError)


async def _generate_semantic(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_retries: int,
    cache_salt_subject: str,
) -> str:
    from models.llm import astream_with_retry

    content: str | None = None
    final_tool_calls: list[dict[str, Any]] = []

    async def stream() -> None:
        nonlocal content, final_tool_calls
        async for chunk in astream_with_retry(
            messages,
            model=model,
            max_retries=max_retries,
            user_id=cache_salt_subject,
        ):
            if chunk.get("type") == "final":
                content = chunk.get("content") or ""
                final_tool_calls = chunk.get("tool_calls") or []

    async with asyncio.timeout(config.COMPACTION_TIMEOUT):
        await stream()
    if final_tool_calls:
        raise RuntimeError("compact model returned tool calls despite receiving no tools")
    if content is None:
        raise RuntimeError("compact model stream ended without a final response")
    content = content.strip()
    if not content:
        raise RuntimeError("compact model produced an empty summary")
    return content


async def _run_generate_task(
    *,
    task: CheckpointTask,
    checkpoint: Checkpoint,
    reader: TranscriptReader,
    args: argparse.Namespace,
    compact_agent,
    semantic_model: str,
    semaphore: asyncio.Semaphore,
    progress: ProgressReporter,
) -> None:
    acquired = False
    if task.summary_kind == "semantic":
        await semaphore.acquire()
        acquired = True
    try:
        attempts = checkpoint.claim_task(task, retry_failed=args.retry_failed)
    except Exception:
        if acquired:
            semaphore.release()
        raise
    if attempts is None:
        if acquired:
            semaphore.release()
        return
    await progress.task_started()
    success = False
    try:
        transcript = await reader.load(
            conversation_id=task.conversation_id,
            leaf_message_id=task.leaf_message_id,
            expected_path_message_count=args.path_counts[
                (task.conversation_id, task.leaf_message_id)
            ],
        )
        if task.summary_kind == "mechanical":
            content = build_mechanical_summary(
                transcript,
                max_chars=args.mechanical_max_chars,
                recent_turns=args.mechanical_recent_turns,
                field_max_chars=args.mechanical_field_max_chars,
            )
        elif args.skip_semantic:
            raise RuntimeError("semantic summary skipped by operator")
        else:
            messages = build_semantic_messages(
                transcript,
                system_prompt=compact_agent.role_prompt,
                max_chars=args.semantic_input_max_chars,
                recent_turns=args.semantic_recent_turns,
                field_max_chars=args.semantic_field_max_chars,
            )
            content = await _generate_semantic(
                messages,
                model=semantic_model,
                max_retries=args.max_retries,
                # The stopped migration has no authenticated request user. Use a
                # deterministic per-conversation isolation principal instead:
                # this is stricter than per-user sharing and still produces the
                # same opaque HMAC cache salt on retries/resume.
                cache_salt_subject=(
                    f"native-history-migration:{task.conversation_id}"
                ),
            )
        checkpoint.set_task_result(
            migration_id=task.migration_id,
            conversation_id=task.conversation_id,
            leaf_message_id=task.leaf_message_id,
            summary_kind=task.summary_kind,
            status="succeeded",
            attempts=attempts,
            summary_content=content,
        )
        success = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = _checkpoint_error(exc)
        expected_failure = _is_expected_task_failure(exc)
        checkpoint.set_task_result(
            migration_id=task.migration_id,
            conversation_id=task.conversation_id,
            leaf_message_id=task.leaf_message_id,
            summary_kind=task.summary_kind,
            status="failed",
            attempts=attempts,
            error=error,
        )
        print(
            f"{'WARNING' if expected_failure else 'ERROR'} generate task failed: "
            f"migration={task.migration_id!r} "
            f"conversation={task.conversation_id!r} "
            f"leaf={task.leaf_message_id!r} kind={task.summary_kind!r} "
            f"attempt={attempts} error={error}",
            file=sys.stderr,
            flush=True,
        )
        if not expected_failure:
            traceback.print_exception(
                type(exc), exc, exc.__traceback__, file=sys.stderr
            )
    finally:
        await progress.task_finished(success=success)
        if acquired:
            semaphore.release()


async def _generate(args: argparse.Namespace) -> int:
    database_url = resolve_database_url()
    _require_postgresql_source(database_url)
    checkpoint = Checkpoint(args.checkpoint.resolve())
    assert_source_database(checkpoint, args.migration_id, database_url)
    run_status = checkpoint.get_run_status(args.migration_id)
    if run_status == "applied":
        raise CheckpointError("migration is already applied; generation is closed")
    if run_status == "generating" and not args.resume:
        raise CheckpointError("generation was already started; pass --resume")
    if run_status == "ready" and not args.retry_failed:
        _print_report(checkpoint.report(args.migration_id))
        return 0
    if args.retry_failed and not args.resume:
        raise CheckpointError("--retry-failed requires --resume")
    checkpoint.set_run_status(args.migration_id, "generating")

    eligible = {"pending", "running"}
    if args.retry_failed:
        eligible.add("failed")
    all_tasks = checkpoint.list_tasks(args.migration_id)
    tasks = [task for task in all_tasks if task.status in eligible]
    if not tasks:
        report = checkpoint.report(args.migration_id)
        if report["ready_for_apply"]:
            checkpoint.set_run_status(args.migration_id, "ready")
            report = checkpoint.report(args.migration_id)
        _print_report(report)
        return 0 if report["ready_for_apply"] else 1

    # The immutable manifest owns the expected path sizes. Pull them from the
    # checkpoint, not from a fresh leaf scan.
    args.path_counts = checkpoint.manifest_path_counts(args.migration_id)

    semantic_tasks = [task for task in tasks if task.summary_kind == "semantic"]
    compact_agent = None
    semantic_model = ""
    if semantic_tasks and not args.skip_semantic:
        compact_agent = load_agent(str(ROOT / "config" / "agents" / "compact_agent.md"))
        semantic_model = args.semantic_model or compact_agent.model
        print(
            f"semantic model={semantic_model!r} tasks={len(semantic_tasks)} "
            f"concurrency={args.concurrency}",
            flush=True,
        )

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        reader = TranscriptReader(engine)
        progress = ProgressReporter(
            len(all_tasks),
            succeeded=sum(task.status == "succeeded" for task in all_tasks),
            failed=sum(
                task.status == "failed" and not args.retry_failed
                for task in all_tasks
            ),
        )
        semaphore = asyncio.Semaphore(args.concurrency)

        # Finish deterministic local fallbacks first. If the model endpoint is
        # unavailable later, every leaf already has its best-effort candidate.
        mechanical = [task for task in tasks if task.summary_kind == "mechanical"]
        semantic = [task for task in tasks if task.summary_kind == "semantic"]
        for task in mechanical:
            await _run_generate_task(
                task=task,
                checkpoint=checkpoint,
                reader=reader,
                args=args,
                compact_agent=compact_agent,
                semantic_model=semantic_model,
                semaphore=semaphore,
                progress=progress,
            )
        await asyncio.gather(*[
            _run_generate_task(
                task=task,
                checkpoint=checkpoint,
                reader=reader,
                args=args,
                compact_agent=compact_agent,
                semantic_model=semantic_model,
                semaphore=semaphore,
                progress=progress,
            )
            for task in semantic
        ])
    finally:
        await engine.dispose()

    report = checkpoint.report(args.migration_id)
    if report["ready_for_apply"]:
        checkpoint.set_run_status(args.migration_id, "ready")
        report = checkpoint.report(args.migration_id)
    _print_report(report)
    return 0 if report["ready_for_apply"] else 1


async def _apply(args: argparse.Namespace) -> int:
    if not args.confirm_backend_stopped:
        raise RuntimeError("apply requires --confirm-backend-stopped")
    database_url = resolve_database_url()
    _require_postgresql_source(database_url)
    checkpoint = Checkpoint(args.checkpoint.resolve())
    assert_source_database(checkpoint, args.migration_id, database_url)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        result = await apply_boundaries(engine, checkpoint, args.migration_id)
    finally:
        await engine.dispose()
    print(
        f"apply: total={result.total} inserted={result.inserted} "
        f"already_present={result.already_present}"
    )
    return 0


async def _verify(args: argparse.Namespace) -> int:
    database_url = resolve_database_url()
    _require_postgresql_source(database_url)
    checkpoint = Checkpoint(args.checkpoint.resolve())
    assert_source_database(checkpoint, args.migration_id, database_url)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        result = await verify_boundaries(engine, checkpoint, args.migration_id)
    finally:
        await engine.dispose()
    print(
        f"verify: total={result.total} verified={result.verified} "
        f"success={str(result.total == result.verified).lower()}"
    )
    return 0 if result.total == result.verified else 1


def _common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--checkpoint", type=Path, required=True)
    subparser.add_argument("--migration-id", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan the fully stopped source DB once.")
    _common(scan)
    scan.add_argument(
        "--resume",
        action="store_true",
        help="Reuse an existing immutable scan; do not query the source DB again.",
    )

    generate = subparsers.add_parser(
        "generate",
        help="Generate resumable mechanical and active-leaf semantic candidates.",
    )
    _common(generate)
    generate.add_argument("--resume", action="store_true")
    generate.add_argument("--retry-failed", action="store_true")
    generate.add_argument("--skip-semantic", action="store_true")
    generate.add_argument("--semantic-model")
    generate.add_argument("--concurrency", type=int, default=2)
    generate.add_argument("--max-retries", type=int, default=3)
    generate.add_argument(
        "--mechanical-max-chars", type=int, default=DEFAULT_MECHANICAL_MAX_CHARS
    )
    generate.add_argument(
        "--mechanical-recent-turns", type=int, default=DEFAULT_MECHANICAL_RECENT_TURNS
    )
    generate.add_argument(
        "--mechanical-field-max-chars",
        type=int,
        default=DEFAULT_MECHANICAL_FIELD_MAX_CHARS,
    )
    generate.add_argument(
        "--semantic-input-max-chars",
        type=int,
        default=DEFAULT_SEMANTIC_INPUT_MAX_CHARS,
    )
    generate.add_argument(
        "--semantic-recent-turns", type=int, default=DEFAULT_SEMANTIC_RECENT_TURNS
    )
    generate.add_argument(
        "--semantic-field-max-chars",
        type=int,
        default=DEFAULT_SEMANTIC_FIELD_MAX_CHARS,
    )

    report = subparsers.add_parser("report", help="Read one checkpoint report.")
    _common(report)
    report.add_argument("--json", action="store_true")

    apply_parser = subparsers.add_parser(
        "apply", help="Append deterministic boundary pairs to the stopped source DB."
    )
    _common(apply_parser)
    apply_parser.add_argument("--confirm-backend-stopped", action="store_true")

    verify = subparsers.add_parser(
        "verify", help="Verify every selected leaf boundary against the source DB."
    )
    _common(verify)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.command != "generate":
        return
    if args.concurrency < 1:
        raise ValueError("--concurrency must be at least 1")
    if args.max_retries < 1:
        raise ValueError("--max-retries must be at least 1")
    for label, max_chars, recent_turns, field_max_chars in (
        (
            "mechanical",
            args.mechanical_max_chars,
            args.mechanical_recent_turns,
            args.mechanical_field_max_chars,
        ),
        (
            "semantic",
            args.semantic_input_max_chars,
            args.semantic_recent_turns,
            args.semantic_field_max_chars,
        ),
    ):
        if max_chars < 2_000:
            raise ValueError(f"--{label}-max-chars must be at least 2000")
        if recent_turns < 1:
            raise ValueError(f"--{label}-recent-turns must be at least 1")
        if field_max_chars < 100:
            raise ValueError(f"--{label}-field-max-chars must be at least 100")
        if max_chars < 4 * field_max_chars + 2_000:
            raise ValueError(
                f"{label} max chars must be at least 4 * field max chars + 2000 "
                "so the first and newest turns always fit"
            )


def main() -> None:
    args = build_parser().parse_args()
    try:
        _validate_args(args)
        if args.command == "scan":
            code = asyncio.run(_scan(args))
        elif args.command == "generate":
            code = asyncio.run(_generate(args))
        elif args.command == "report":
            code = _report(args)
        elif args.command == "apply":
            code = asyncio.run(_apply(args))
        else:
            code = asyncio.run(_verify(args))
    except (BoundaryError, CheckpointError, ManifestError, TranscriptError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()

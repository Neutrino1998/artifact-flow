"""Bounded readers for the admin instance-event drawer.

The instance status signals already have durable homes:

* ``ERROR`` count/time comes from the ``ArtifactFlow`` logger, whose ERROR+
  records are duplicated into ``artifactflow_error.log``.
* loop-lag / hard-wedge records live in ``loop-lag.jsonl``.
* runtime samples live in ``metrics.jsonl``.

This module reads those same files on demand.  It deliberately does not merge
``MessageEvent`` rows: doing so would give one execution failure two sources of
truth and require heuristic de-duplication.  IDs already frozen into the log
prefix are enough for the UI to link back to conversation observability.

All functions here are synchronous; the API boundary must call
``asyncio.to_thread``.  Reads are capped by bytes, event count, task count and
detail length so observability cannot become a new event-loop or memory hazard.
"""

from __future__ import annotations

import bisect
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from config import config


_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_LOG_HEADER_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r" - (?P<logger>.+?) - (?P<level>ERROR|CRITICAL)"
    r" - \[(?P<context>[^]]*)] (?P<location>.+?) - (?P<summary>.*)$"
)
_EXEC_TASK_RE = re.compile(r"^exec-(msg-[A-Za-z0-9._-]+)$")

InstanceEventKind = Literal["all", "error", "wedge", "loop_lag"]
_EVENT_KINDS = {"all", "error", "wedge", "loop_lag"}


def is_safe_instance_id(instance_id: str) -> bool:
    """Match the path-safe identity contract in ``utils.instance._mint``."""
    return bool(_INSTANCE_ID_RE.fullmatch(instance_id)) and instance_id not in {".", ".."}


def _safe_scoped_file(root: Path, instance_id: str, filename: str) -> Path:
    """Resolve one instance child under ``root`` without trusting symlinks."""
    resolved_root = root.resolve()
    candidate = (resolved_root / instance_id / filename).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("instance path escapes configured root") from exc
    return candidate


def _instance_scoped_path(configured: str, instance_id: str) -> Path:
    path = Path(configured)
    return _safe_scoped_file(path.parent, instance_id, path.name)


def _error_log_path(instance_id: str) -> Path:
    root = Path(os.environ.get("ARTIFACTFLOW_LOG_DIR") or "data/logs")
    return _safe_scoped_file(root, instance_id, "artifactflow_error.log")


def _rotated_paths_newest_first(base: Path, backups: int) -> list[Path]:
    return [base, *(Path(f"{base}.{i}") for i in range(1, backups + 1))]


def _read_file_tail(path: Path, budget: int) -> tuple[str, int, bool]:
    """Read at most ``budget`` bytes from one file's tail.

    Returns ``(text, bytes_examined, cut_at_front)``.  A partial first line is
    discarded so neither JSON nor a logging header is fabricated from a byte
    boundary in the middle of a record.
    """
    try:
        size = path.stat().st_size
        take = min(size, budget)
        start = size - take
        with path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(take)
    except (FileNotFoundError, OSError):
        return "", 0, False

    cut = start > 0
    if cut:
        newline = raw.find(b"\n")
        raw = raw[newline + 1 :] if newline >= 0 else b""
    return raw.decode("utf-8", errors="replace"), take, cut


def _read_rotated_tail(base: Path) -> tuple[str, bool, bool]:
    """Read newest retained bytes across ``base``, ``.1`` ... ``.N``.

    Files are consumed newest-first to spend the budget on the most useful
    evidence, then concatenated in chronological order for record parsing.
    """
    remaining = int(config.OBS_ADMIN_EVENT_SCAN_MAX_BYTES)
    newest_chunks: list[str] = []
    available = False
    truncated = False

    for path in _rotated_paths_newest_first(base, config.OBS_JSONL_BACKUP_COUNT):
        if remaining <= 0:
            try:
                truncated = truncated or path.stat().st_size > 0
                available = True
            except OSError:
                pass
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        available = True
        text, examined, cut = _read_file_tail(path, remaining)
        remaining -= examined
        truncated = truncated or cut
        if text:
            newest_chunks.append(text)

    return "\n".join(reversed(newest_chunks)), available, truncated


def _clean_context_id(value: Optional[str]) -> Optional[str]:
    if not value or value in {"no-ctx", "no-req", "-"}:
        return None
    return value


def _log_ts_as_utc(raw: str) -> str:
    """Normalize stdlib logging's server-local ``asctime`` to naive UTC."""
    local_naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S,%f")
    local_aware = local_naive.astimezone()
    return local_aware.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def _parse_error_log(text: str, instance_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    detail_lines: list[str] = []
    detail_chars = 0
    max_detail = int(config.OBS_ADMIN_EVENT_DETAIL_MAX_CHARS)

    def flush() -> None:
        nonlocal current, detail_lines, detail_chars
        if current is None:
            return
        detail = "\n".join(detail_lines).strip()
        current["detail"] = detail or None
        current["id"] = f"runtime-{len(events)}-{current['ts']}"
        events.append(current)
        current = None
        detail_lines = []
        detail_chars = 0

    for line in text.splitlines():
        match = _LOG_HEADER_RE.match(line)
        if match:
            flush()
            context = match.group("context").split("|")
            request_id = conversation_id = message_id = None
            if len(context) == 4:
                # Current contract: instance | request | conversation | message.
                request_id = _clean_context_id(context[1])
                conversation_id = _clean_context_id(context[2])
                message_id = _clean_context_id(context[3])
            try:
                ts = _log_ts_as_utc(match.group("ts"))
            except ValueError:
                continue
            summary = match.group("summary")[:max_detail]
            current = {
                "type": "error",
                "source": "runtime_log",
                "severity": "error",
                "ts": ts,
                "summary": summary,
                "level": match.group("level"),
                "location": match.group("location")[:1000],
                "request_id": request_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "instance_id": instance_id,
            }
            continue

        # Tracebacks and wrapped exception text belong to the preceding header.
        if current is not None and detail_chars < max_detail:
            remaining = max_detail - detail_chars
            clipped = line[:remaining]
            detail_lines.append(clipped)
            detail_chars += len(clipped) + 1

    flush()
    return events


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _bounded_stack_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    remaining = int(config.OBS_ADMIN_EVENT_DETAIL_MAX_CHARS)
    out: list[str] = []
    for line in value[:8]:
        if remaining <= 0:
            break
        clipped = str(line)[: min(1000, remaining)]
        out.append(clipped)
        remaining -= len(clipped)
    return out


def _bounded_tasks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    tasks = [task for task in value if isinstance(task, dict)]
    # Execution tasks carry the message locator operators care about most.
    # Preserve their input order within the priority groups.
    tasks.sort(key=lambda task: not str(task.get("name") or "").startswith("exec-msg-"))
    for task in tasks[: config.OBS_ADMIN_EVENT_MAX_TASKS]:
        out.append({
            "name": str(task.get("name") or "unnamed")[:256],
            "done": bool(task.get("done", False)),
            "stack": _bounded_stack_list(task.get("stack")),
        })
    return out


def _bounded_threads(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    threads = [thread for thread in value if isinstance(thread, dict)]
    threads.sort(key=lambda thread: not bool(thread.get("event_loop", False)))
    for thread in threads[: config.OBS_ADMIN_EVENT_MAX_TASKS]:
        out.append({
            "name": str(thread.get("name") or "unnamed")[:256],
            "event_loop": bool(thread.get("event_loop", False)),
            "stack": _bounded_stack_list(thread.get("stack")),
        })
    return out


def _bounded_active_message_ids(value: Any) -> list[str]:
    """Return a bounded, deterministic list from a new-format loop record."""
    if not isinstance(value, list):
        return []
    valid = {
        item
        for item in value
        if isinstance(item, str) and _EXEC_TASK_RE.fullmatch(f"exec-{item}")
    }
    return sorted(valid)[: config.OBS_ADMIN_EVENT_MAX_TASKS]


def _parse_loop_events(text: str, instance_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    max_detail = int(config.OBS_ADMIN_EVENT_DETAIL_MAX_CHARS)
    for record in _parse_jsonl(text):
        ts = record.get("ts")
        if not isinstance(ts, str):
            continue
        tasks = _bounded_tasks(record.get("tasks"))
        threads = _bounded_threads(record.get("threads"))
        active_message_ids = _bounded_active_message_ids(
            record.get("active_message_ids")
        )
        wedged = bool(record.get("wedged", False))
        lag_ms = record.get("lag_ms")
        loop_thread = next((thread for thread in threads if thread["event_loop"]), None)
        suspected_location = None
        if loop_thread and loop_thread["stack"]:
            suspected_location = loop_thread["stack"][-1]
        summary = (
            f"事件循环至少 {lag_ms}ms 未响应"
            if wedged
            else f"事件循环调度延迟达到 {lag_ms}ms"
        )
        events.append({
            "id": f"loop-{len(events)}-{ts}",
            "type": "wedge" if wedged else "loop_lag",
            "source": "loop_lag",
            "severity": "error" if wedged else "warning",
            "ts": ts,
            "summary": summary[:max_detail],
            "lag_ms": lag_ms,
            "lower_bound": wedged,
            "warn_ms": record.get("warn_ms"),
            "location": suspected_location,
            "request_id": None,
            "conversation_id": None,
            # A lag pauses the entire event loop.  Concurrent execution tasks
            # are context, not proof that an arbitrary first task caused it.
            "message_id": None,
            "active_message_ids": active_message_ids,
            "instance_id": instance_id,
            "tasks": tasks,
            "threads": threads,
        })
    return events


def _metric_summary(record: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if record is None:
        return None
    return {
        "ts": record.get("ts"),
        "loop_lag_ms": record.get("loop_lag_ms") or {},
        "in_flight": record.get("in_flight"),
        "tasks_long_running": record.get("tasks_long_running"),
        "process": record.get("process") or {},
        "db_pool": record.get("db_pool") or {},
        "redis": record.get("redis") or {},
    }


def _attach_nearest_metrics(
    events: list[dict[str, Any]],
    metric_records: list[dict[str, Any]],
) -> None:
    stamped: list[tuple[datetime, dict[str, Any]]] = []
    for record in metric_records:
        ts = record.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            stamped.append((datetime.fromisoformat(ts), record))
        except ValueError:
            continue
    stamped.sort(key=lambda pair: pair[0])
    metric_times = [pair[0] for pair in stamped]
    nearby_sec = max(float(config.OBS_SAMPLE_INTERVAL_SEC) * 3, 60.0)

    for event in events:
        if event.get("type") not in {"wedge", "loop_lag"}:
            continue
        try:
            event_time = datetime.fromisoformat(str(event["ts"]))
        except (KeyError, ValueError):
            continue
        index = bisect.bisect_left(metric_times, event_time)
        before = stamped[index - 1][1] if index > 0 else None
        after = stamped[index][1] if index < len(stamped) else None
        if before is not None:
            before_time = stamped[index - 1][0]
            if (event_time - before_time).total_seconds() > nearby_sec:
                before = None
        if after is not None:
            after_time = stamped[index][0]
            if (after_time - event_time).total_seconds() > nearby_sec:
                after = None
        event["metrics_before"] = _metric_summary(before)
        event["metrics_after"] = _metric_summary(after)


def _event_sort_key(event: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(event.get("ts") or ""))
    except ValueError:
        return datetime.min


def read_instance_events(
    instance_id: str,
    limit: int,
    kind: InstanceEventKind = "all",
) -> dict[str, Any]:
    """Read and normalize one instance's most recent diagnostic events."""
    if not is_safe_instance_id(instance_id):
        raise ValueError("invalid instance_id")
    if kind not in _EVENT_KINDS:
        raise ValueError("invalid event kind")
    limit = max(1, min(int(limit), int(config.OBS_ADMIN_EVENT_LIMIT_MAX)))

    error_text, error_available, error_truncated = _read_rotated_tail(
        _error_log_path(instance_id)
    )
    loop_text, loop_available, loop_truncated = _read_rotated_tail(
        _instance_scoped_path(config.OBS_LOOP_LAG_LOG_PATH, instance_id)
    )
    metrics_text, metrics_available, metrics_truncated = _read_rotated_tail(
        _instance_scoped_path(config.OBS_METRICS_LOG_PATH, instance_id)
    )

    events = _parse_error_log(error_text, instance_id)
    loop_events = _parse_loop_events(loop_text, instance_id)
    _attach_nearest_metrics(loop_events, _parse_jsonl(metrics_text))
    events.extend(loop_events)
    if kind != "all":
        # Apply the selected type before the result limit.  Otherwise a burst of
        # newer ERROR or soft-lag records can make a retained hard wedge appear
        # to be absent when the user opens its exact filter.
        events = [event for event in events if event.get("type") == kind]
    events.sort(key=_event_sort_key, reverse=True)

    return {
        "instance_id": instance_id,
        "events": events[:limit],
        "sources": {
            "error_log": {"configured": True, "available": error_available, "truncated": error_truncated},
            "loop_lag": {"configured": True, "available": loop_available, "truncated": loop_truncated},
            "metrics": {"configured": True, "available": metrics_available, "truncated": metrics_truncated},
        },
    }

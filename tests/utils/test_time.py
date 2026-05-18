"""Regression tests for the project-wide naive-UTC time convention.

Background: incident 2026-05-14 PR-tz-unify. Pre-PR, hot-path callsites
used `datetime.now()` (local naive) while DB `server_default=func.now()`
gave UTC naive on SQLite — mixed convention drifted obs queries by the
deployment TZ offset (8h on Shanghai). After PR, all Python-side writes
go through `utils.time.utc_now()`.

These tests defend against a revert of either:
  - `utc_now()` itself becoming local
  - `ExecutionEvent.created_at` default reverting to `datetime.now`
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.events import ExecutionEvent
from utils.time import utc_now


def test_utc_now_returns_naive_datetime():
    """Naive (tzinfo=None) is the project convention — schema columns are
    `DateTime` without `timezone=True`, and `observability_report.py`
    threshold compares against naive values."""
    now = utc_now()
    assert now.tzinfo is None, (
        f"utc_now() must return naive datetime; got tzinfo={now.tzinfo!r}. "
        "Naive UTC is the project convention (see utils/time.py docstring)."
    )


def test_utc_now_matches_utc_wall_clock():
    """The naive value must equal current UTC, NOT local time. On a
    non-UTC machine these differ by the TZ offset — the very bug
    PR-tz-unify fixed."""
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    now = utc_now()
    after = datetime.now(timezone.utc).replace(tzinfo=None)
    # Sanity: utc_now is bracketed by two UTC reads taken right around it.
    assert before <= now <= after, (
        f"utc_now()={now} not within UTC wall-clock window "
        f"[{before}, {after}]. If this fails on a non-UTC machine, "
        "utc_now() has likely been reverted to local time."
    )


def test_execution_event_created_at_is_utc_naive():
    """ExecutionEvent uses utc_now as default_factory — events.py:55. A
    revert to `datetime.now` would re-introduce the Shanghai-local-naive
    rows that broke obs window queries. This catches that revert."""
    ev = ExecutionEvent(event_type="test")
    assert ev.created_at.tzinfo is None
    # Within a few seconds of UTC wall clock.
    utc_wall = datetime.now(timezone.utc).replace(tzinfo=None)
    drift = abs((utc_wall - ev.created_at).total_seconds())
    assert drift < 5, (
        f"ExecutionEvent.created_at drifts {drift}s from UTC wall clock — "
        "default_factory probably reverted to datetime.now (local). "
        "Restore to utils.time.utc_now."
    )

import { describe, test, expect } from 'vitest';
import { parseUtcIso } from './time';

/**
 * Regression tests for the naive-ISO → UTC parsing helper.
 *
 * Background: incident 2026-05-14 PR-tz-unify. Backend `utils.time.utc_now()`
 * writes naive UTC strings (no `Z`, no offset). `new Date(<naive>)` parses
 * those as **local** time, double-converting on the way to `toLocaleString()`
 * and showing values off by the browser's TZ offset. `parseUtcIso` is the
 * single-point fix that anchors naive strings as UTC.
 */

describe('parseUtcIso', () => {
  test('naive ISO is parsed as UTC, not local', () => {
    // Backend ships `"2026-05-18T08:30:00"` for an event that logically
    // happened at 08:30 UTC. Without the fix, `new Date(...)` would
    // interpret this as 08:30 local — wrong everywhere except UTC machines.
    const d = parseUtcIso('2026-05-18T08:30:00');
    expect(d.toISOString()).toBe('2026-05-18T08:30:00.000Z');
    expect(d.getUTCHours()).toBe(8);
  });

  test('naive ISO with microseconds is parsed as UTC', () => {
    // utc_now().isoformat() produces "...T08:30:00.123456" (6-digit usec).
    // JS Date keeps ms precision but the UTC anchor must still work.
    const d = parseUtcIso('2026-05-18T08:30:00.123456');
    expect(d.getUTCHours()).toBe(8);
    expect(d.getUTCMinutes()).toBe(30);
  });

  test('already-Z-suffixed ISO is left alone (no double Z)', () => {
    // jsonl files and historical aware-UTC payloads may still carry `Z`.
    // Appending another `Z` would yield an invalid date.
    const d = parseUtcIso('2026-05-18T08:30:00Z');
    expect(Number.isNaN(d.getTime())).toBe(false);
    expect(d.toISOString()).toBe('2026-05-18T08:30:00.000Z');
  });

  test('ISO with explicit +HH:MM offset is preserved', () => {
    // Some legacy payloads (older rows / external sources) carry
    // explicit offsets — must NOT have `Z` smashed onto them.
    const d = parseUtcIso('2026-05-18T16:30:00+08:00');
    expect(d.toISOString()).toBe('2026-05-18T08:30:00.000Z');
  });

  test('ISO with -HHMM compact offset is preserved', () => {
    // RFC 3339 allows the compact `-0500` form too. The regex must match
    // both compact and colon-separated offsets.
    const d = parseUtcIso('2026-05-18T03:30:00-0500');
    expect(d.toISOString()).toBe('2026-05-18T08:30:00.000Z');
  });
});

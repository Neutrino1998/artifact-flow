import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchNotifications, fetchWelcomeTips, dismissNotification } from './siteConfig';

/**
 * Tests for siteConfig 静态配置 fetcher。
 *
 * 覆盖 reviewer 反馈的两个洞：
 *  - starts_at/ends_at 坏值要 fail-closed（整条丢弃，而不是当作"无边界"）
 *  - severity / id / title / body schema 错位也要丢弃
 *
 * 加上常规 happy path：时间窗、dismiss 记忆、severity 排序。
 */

const NOTIF_URL = '/site/notifications.json';
const TIPS_URL = '/site/welcome_tips.json';

function mockFetchJson(url: string, body: unknown, ok = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo) => {
      if (typeof input === 'string' && input === url) {
        return new Response(JSON.stringify(body), { status: ok ? 200 : 500 });
      }
      return new Response('not found', { status: 404 });
    }),
  );
}

function mockFetch404() {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 404 })));
}

function mockFetchRaw(url: string, rawBody: string, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo) => {
      if (typeof input === 'string' && input === url) {
        return new Response(rawBody, { status });
      }
      return new Response('', { status: 404 });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ============================================================
// fetchNotifications: schema 校验 (P2 #1)
// ============================================================

describe('fetchNotifications: schema validation', () => {
  test('drops notification with unparseable starts_at', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'n1', severity: 'info', title: 't', body: 'b', starts_at: 'not-a-date' },
      { id: 'n2', severity: 'info', title: 't2', body: 'b2' },
    ]);
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['n2']);
  });

  test('drops notification with unparseable ends_at', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'n1', severity: 'info', title: 't', body: 'b', ends_at: 'tomorrow' },
      { id: 'n2', severity: 'info', title: 't2', body: 'b2' },
    ]);
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['n2']);
  });

  test('drops notification with non-string date types', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'n1', severity: 'info', title: 't', body: 'b', starts_at: 1700000000 },
    ]);
    const result = await fetchNotifications();
    expect(result).toEqual([]);
  });

  test('keeps notification when optional dates are omitted', async () => {
    mockFetchJson(NOTIF_URL, [{ id: 'n1', severity: 'info', title: 't', body: 'b' }]);
    const result = await fetchNotifications();
    expect(result).toHaveLength(1);
  });

  test('drops notification with missing required fields', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'n1', severity: 'info' }, // missing title/body
      { severity: 'info', title: 't', body: 'b' }, // missing id
      { id: 'n2', severity: 'info', title: 't', body: 'b' }, // valid
    ]);
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['n2']);
  });

  test('drops notification with invalid severity', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'n1', severity: 'urgent', title: 't', body: 'b' },
      { id: 'n2', severity: 'critical', title: 't', body: 'b' },
    ]);
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['n2']);
  });
});

// ============================================================
// fetchNotifications: 时间窗过滤
// ============================================================

describe('fetchNotifications: time window', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-12T10:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test('hides notification before starts_at', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'future', severity: 'info', title: 't', body: 'b', starts_at: '2026-05-20T00:00:00Z' },
    ]);
    const result = await fetchNotifications();
    expect(result).toEqual([]);
  });

  test('hides notification after ends_at', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'expired', severity: 'info', title: 't', body: 'b', ends_at: '2026-05-01T00:00:00Z' },
    ]);
    const result = await fetchNotifications();
    expect(result).toEqual([]);
  });

  test('shows notification within window', async () => {
    mockFetchJson(NOTIF_URL, [
      {
        id: 'active',
        severity: 'info',
        title: 't',
        body: 'b',
        starts_at: '2026-05-10T00:00:00Z',
        ends_at: '2026-05-15T00:00:00Z',
      },
    ]);
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['active']);
  });
});

// ============================================================
// dismissNotification 持久化
// ============================================================

describe('fetchNotifications: dismiss persistence', () => {
  test('dismissed dismissible notifications are filtered out', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'n1', severity: 'info', title: 't', body: 'b' },
      { id: 'n2', severity: 'info', title: 't2', body: 'b2' },
    ]);
    dismissNotification('n1');
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['n2']);
  });

  test('dismissible=false notifications survive dismiss', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'forced', severity: 'critical', title: 't', body: 'b', dismissible: false },
    ]);
    dismissNotification('forced');
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['forced']);
  });
});

// ============================================================
// Severity 排序
// ============================================================

describe('fetchNotifications: severity sort', () => {
  test('sorts critical > warn > info, stable within rank', async () => {
    mockFetchJson(NOTIF_URL, [
      { id: 'i1', severity: 'info', title: 't', body: 'b' },
      { id: 'c1', severity: 'critical', title: 't', body: 'b' },
      { id: 'w1', severity: 'warn', title: 't', body: 'b' },
      { id: 'i2', severity: 'info', title: 't', body: 'b' },
      { id: 'c2', severity: 'critical', title: 't', body: 'b' },
    ]);
    const result = await fetchNotifications();
    expect(result.map((n) => n.id)).toEqual(['c1', 'c2', 'w1', 'i1', 'i2']);
  });
});

// ============================================================
// Fetch failure 容错
// ============================================================

describe('fetchNotifications: failure modes', () => {
  test('returns [] on 404', async () => {
    mockFetch404();
    expect(await fetchNotifications()).toEqual([]);
  });

  test('returns [] on invalid JSON', async () => {
    mockFetchRaw(NOTIF_URL, 'not json {{{');
    expect(await fetchNotifications()).toEqual([]);
  });

  test('returns [] when top level is not an array', async () => {
    mockFetchJson(NOTIF_URL, { id: 'n1' });
    expect(await fetchNotifications()).toEqual([]);
  });
});

// ============================================================
// fetchWelcomeTips
// ============================================================

describe('fetchWelcomeTips', () => {
  test('returns string array on happy path', async () => {
    mockFetchJson(TIPS_URL, ['tip 1', 'tip 2']);
    expect(await fetchWelcomeTips()).toEqual(['tip 1', 'tip 2']);
  });

  test('filters non-string and empty entries', async () => {
    mockFetchJson(TIPS_URL, ['ok', 42, '', '   ', null, 'fine']);
    expect(await fetchWelcomeTips()).toEqual(['ok', 'fine']);
  });

  test('returns [] on 404', async () => {
    mockFetch404();
    expect(await fetchWelcomeTips()).toEqual([]);
  });

  test('returns [] on non-array', async () => {
    mockFetchJson(TIPS_URL, { tips: ['x'] });
    expect(await fetchWelcomeTips()).toEqual([]);
  });
});

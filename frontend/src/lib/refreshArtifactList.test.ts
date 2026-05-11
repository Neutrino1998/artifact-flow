import { describe, test, expect, vi, beforeEach } from 'vitest';
import { refreshArtifactList, _resetGenerationForTests } from './refreshArtifactList';
import * as api from './api';
import type { ArtifactSummary } from '@/types';

vi.mock('./api');

/** Build a minimal ArtifactSummary for assertions. */
function art(id: string, version = 1): ArtifactSummary {
  return {
    id,
    content_type: 'text/plain',
    title: id,
    current_version: version,
    source: 'agent',
    original_filename: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
  } as ArtifactSummary;
}

/** Build a deferred promise so the test controls resolution order. */
function deferred<T>(): {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
} {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('refreshArtifactList', () => {
  beforeEach(() => {
    _resetGenerationForTests();
    vi.resetAllMocks();
  });

  test('happy path: response applies via setArtifacts', async () => {
    vi.mocked(api.listArtifacts).mockResolvedValue({
      session_id: 'sess-1',
      artifacts: [art('a')],
    });
    const setArtifacts = vi.fn();
    await refreshArtifactList('sess-1', setArtifacts, () => 'sess-1');
    expect(setArtifacts).toHaveBeenCalledOnce();
    expect(setArtifacts).toHaveBeenCalledWith([art('a')]);
  });

  test('out-of-order: later-fired but earlier-resolved request wins; older response dropped', async () => {
    // Two concurrent calls: A fired first, B fired second.
    // B resolves first with the newer snapshot, then A resolves later with
    // a stale snapshot. The stale resolution must NOT overwrite.
    const dA = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    const dB = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    vi.mocked(api.listArtifacts)
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const setArtifacts = vi.fn();

    const callA = refreshArtifactList('sess-1', setArtifacts, () => 'sess-1');
    const callB = refreshArtifactList('sess-1', setArtifacts, () => 'sess-1');

    // B resolves first → its (newer) result should apply.
    dB.resolve({ session_id: 'sess-1', artifacts: [art('a'), art('b')] });
    await callB;
    expect(setArtifacts).toHaveBeenCalledOnce();
    expect(setArtifacts).toHaveBeenLastCalledWith([art('a'), art('b')]);

    // A resolves later with a stale (single-item) snapshot → must be dropped.
    dA.resolve({ session_id: 'sess-1', artifacts: [art('a')] });
    await callA;
    // setArtifacts is still only the one B-call; no second invocation.
    expect(setArtifacts).toHaveBeenCalledOnce();
  });

  test('session switch: response for old session dropped if user moved to new session', async () => {
    vi.mocked(api.listArtifacts).mockResolvedValue({
      session_id: 'sess-old',
      artifacts: [art('a')],
    });
    const setArtifacts = vi.fn();
    let currentSession = 'sess-old';
    const getCurrent = () => currentSession;

    const call = refreshArtifactList('sess-old', setArtifacts, getCurrent);
    // User switched away while request was in flight
    currentSession = 'sess-new';
    await call;

    expect(setArtifacts).not.toHaveBeenCalled();
  });

  test('null current session is treated as match (new-conversation flow)', async () => {
    vi.mocked(api.listArtifacts).mockResolvedValue({
      session_id: 'sess-1',
      artifacts: [art('a')],
    });
    const setArtifacts = vi.fn();
    // Before the SSE handler stamps sessionId in artifact store, current is null.
    await refreshArtifactList('sess-1', setArtifacts, () => null);
    expect(setArtifacts).toHaveBeenCalledWith([art('a')]);
  });

  test('network error: swallowed silently, no setArtifacts call', async () => {
    vi.mocked(api.listArtifacts).mockRejectedValue(new Error('boom'));
    const setArtifacts = vi.fn();
    await expect(
      refreshArtifactList('sess-1', setArtifacts, () => 'sess-1')
    ).resolves.toBeUndefined();
    expect(setArtifacts).not.toHaveBeenCalled();
  });

  test('three concurrent calls: only the last fired one wins regardless of resolution order', async () => {
    const d1 = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    const d2 = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    const d3 = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    vi.mocked(api.listArtifacts)
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise)
      .mockReturnValueOnce(d3.promise);

    const setArtifacts = vi.fn();
    const c1 = refreshArtifactList('sess-1', setArtifacts, () => 'sess-1');
    const c2 = refreshArtifactList('sess-1', setArtifacts, () => 'sess-1');
    const c3 = refreshArtifactList('sess-1', setArtifacts, () => 'sess-1');

    // Resolve in scrambled order: 2 → 1 → 3
    d2.resolve({ session_id: 'sess-1', artifacts: [art('mid')] });
    await c2;
    d1.resolve({ session_id: 'sess-1', artifacts: [art('oldest')] });
    await c1;
    d3.resolve({ session_id: 'sess-1', artifacts: [art('newest')] });
    await c3;

    // Only call 3 (newest generation) was allowed to write.
    expect(setArtifacts).toHaveBeenCalledOnce();
    expect(setArtifacts).toHaveBeenLastCalledWith([art('newest')]);
  });
});

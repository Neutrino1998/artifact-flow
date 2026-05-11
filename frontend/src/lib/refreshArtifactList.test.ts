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

/** Build a tiny fake sessionId store: tracks current value, supports set/get. */
function makeSessionIdStore(initial: string | null = null) {
  let value = initial;
  return {
    set: vi.fn((v: string | null) => {
      value = v;
    }),
    get: () => value,
    /** test-only helper to simulate external reset() mid-flight */
    externalReset: () => {
      value = null;
    },
  };
}

describe('refreshArtifactList', () => {
  beforeEach(() => {
    _resetGenerationForTests();
    vi.resetAllMocks();
  });

  test('happy path: stamps sessionId and applies artifacts', async () => {
    vi.mocked(api.listArtifacts).mockResolvedValue({
      session_id: 'sess-1',
      artifacts: [art('a')],
    });
    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);

    await refreshArtifactList('sess-1', setArtifacts, session.set, session.get);

    expect(session.set).toHaveBeenCalledWith('sess-1');  // claimed before await
    expect(setArtifacts).toHaveBeenCalledOnce();
    expect(setArtifacts).toHaveBeenCalledWith([art('a')]);
  });

  test('claim-before-await: sessionId is stamped immediately, not after resolve', async () => {
    const d = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    vi.mocked(api.listArtifacts).mockReturnValue(d.promise);
    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);

    const call = refreshArtifactList('sess-1', setArtifacts, session.set, session.get);

    // Before the promise resolves, the session is already claimed
    expect(session.set).toHaveBeenCalledWith('sess-1');
    expect(session.get()).toBe('sess-1');
    // But artifacts have not been written yet
    expect(setArtifacts).not.toHaveBeenCalled();

    d.resolve({ session_id: 'sess-1', artifacts: [art('a')] });
    await call;
    expect(setArtifacts).toHaveBeenCalledWith([art('a')]);
  });

  test('reviewer scenario: reset() mid-flight drops the stale response', async () => {
    // The bug: switchConversation / newConversation calls resetArtifacts(),
    // setting store sessionId to null. An old in-flight request must not
    // setArtifacts() after reset, even when no follow-up refresh fires.
    const d = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    vi.mocked(api.listArtifacts).mockReturnValue(d.promise);
    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);

    const call = refreshArtifactList('sess-old', setArtifacts, session.set, session.get);

    // After claim, store has sess-old
    expect(session.get()).toBe('sess-old');

    // User clicks "new conversation" → reset()
    session.externalReset();
    expect(session.get()).toBe(null);

    // Old request resolves now — must be dropped because cur is null
    d.resolve({ session_id: 'sess-old', artifacts: [art('a')] });
    await call;
    expect(setArtifacts).not.toHaveBeenCalled();
  });

  test('switched to different session: stale response dropped', async () => {
    const d = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    vi.mocked(api.listArtifacts).mockReturnValue(d.promise);
    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);

    const call = refreshArtifactList('sess-old', setArtifacts, session.set, session.get);

    // User switches to a new conversation; another refresh fires and claims sess-new
    session.set('sess-new');

    d.resolve({ session_id: 'sess-old', artifacts: [art('a')] });
    await call;
    expect(setArtifacts).not.toHaveBeenCalled();
  });

  test('out-of-order: newer call wins regardless of resolution order', async () => {
    const dA = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    const dB = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    vi.mocked(api.listArtifacts)
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);

    const callA = refreshArtifactList('sess-1', setArtifacts, session.set, session.get);
    const callB = refreshArtifactList('sess-1', setArtifacts, session.set, session.get);

    // B resolves first with newer data — applies
    dB.resolve({ session_id: 'sess-1', artifacts: [art('a'), art('b')] });
    await callB;
    expect(setArtifacts).toHaveBeenCalledOnce();
    expect(setArtifacts).toHaveBeenLastCalledWith([art('a'), art('b')]);

    // A resolves later with older data — must be dropped
    dA.resolve({ session_id: 'sess-1', artifacts: [art('a')] });
    await callA;
    expect(setArtifacts).toHaveBeenCalledOnce();
  });

  test('three concurrent calls: only latest generation wins', async () => {
    const d1 = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    const d2 = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    const d3 = deferred<{ session_id: string; artifacts: ArtifactSummary[] }>();
    vi.mocked(api.listArtifacts)
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise)
      .mockReturnValueOnce(d3.promise);

    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);

    const c1 = refreshArtifactList('sess-1', setArtifacts, session.set, session.get);
    const c2 = refreshArtifactList('sess-1', setArtifacts, session.set, session.get);
    const c3 = refreshArtifactList('sess-1', setArtifacts, session.set, session.get);

    d2.resolve({ session_id: 'sess-1', artifacts: [art('mid')] });
    await c2;
    d1.resolve({ session_id: 'sess-1', artifacts: [art('oldest')] });
    await c1;
    d3.resolve({ session_id: 'sess-1', artifacts: [art('newest')] });
    await c3;

    expect(setArtifacts).toHaveBeenCalledOnce();
    expect(setArtifacts).toHaveBeenLastCalledWith([art('newest')]);
  });

  test('network error: swallowed silently, no setArtifacts call', async () => {
    vi.mocked(api.listArtifacts).mockRejectedValue(new Error('boom'));
    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);

    await expect(
      refreshArtifactList('sess-1', setArtifacts, session.set, session.get)
    ).resolves.toBeUndefined();
    expect(setArtifacts).not.toHaveBeenCalled();
    // Session was still claimed (stamping happens before await) — this is
    // intentional: if no other concurrent call competes, future refreshes
    // will see the stamp as the current session.
    expect(session.set).toHaveBeenCalledWith('sess-1');
  });

  test('new-convo flow: cur was null at call time, helper still applies', async () => {
    // The previous design needed a null fallthrough to support this case.
    // With claim-before-await, the helper stamps the session itself, so
    // the new-convo flow works without special casing.
    vi.mocked(api.listArtifacts).mockResolvedValue({
      session_id: 'new-conv',
      artifacts: [art('first')],
    });
    const setArtifacts = vi.fn();
    const session = makeSessionIdStore(null);  // start with null (no conv yet)

    await refreshArtifactList('new-conv', setArtifacts, session.set, session.get);

    expect(setArtifacts).toHaveBeenCalledWith([art('first')]);
    expect(session.get()).toBe('new-conv');
  });
});

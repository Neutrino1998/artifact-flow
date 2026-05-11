import { describe, test, expect, vi, beforeEach } from 'vitest';
import { autoOpenArtifact, type ArtifactAutoOpenDeps } from './artifactAutoOpen';
import {
  bumpArtifactFetchGen,
  _resetArtifactFetchGenForTests,
} from './artifactFetchGen';
import type { ArtifactDetail, VersionSummary } from '@/types';

/** Build a minimal ArtifactDetail for tests. */
function detail(id: string, version = 1, contentType = 'text/plain'): ArtifactDetail {
  return {
    id,
    session_id: 'sess-1',
    content_type: contentType,
    title: id,
    content: `${id}@v${version}`,
    current_version: version,
    source: null,
    versions: [
      { version, update_type: 'create' } as VersionSummary,
    ],
  } as ArtifactDetail;
}

/** Deferred promise so the test controls resolution order. */
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

/** Fake artifact-store accessors backed by local mutable state. */
function makeFakeStore(initial: {
  current?: ArtifactDetail | null;
  autoSelected?: boolean;
}) {
  let current = initial.current ?? null;
  let autoSelected = initial.autoSelected ?? false;

  const setCurrentAuto = vi.fn((artifact: ArtifactDetail) => {
    current = artifact;
    autoSelected = true;
  });
  const refreshCurrent = vi.fn((artifact: ArtifactDetail) => {
    if (current && current.id === artifact.id) {
      current = artifact;
      // autoSelected NOT touched — that's the whole point.
    }
  });
  const setVersions = vi.fn();
  const setSelectedVersion = vi.fn();

  const deps: ArtifactAutoOpenDeps = {
    getCurrent: () => current,
    getAutoSelected: () => autoSelected,
    setCurrentAuto,
    refreshCurrent,
    setVersions,
    setSelectedVersion,
  };

  return {
    deps,
    setCurrentAuto,
    refreshCurrent,
    setVersions,
    setSelectedVersion,
    /** Read-only peek for assertions. */
    snapshot: () => ({ current, autoSelected }),
  };
}

describe('autoOpenArtifact', () => {
  beforeEach(() => {
    _resetArtifactFetchGenForTests();
  });

  test('happy path: cur=null → setCurrentAuto + versions + selectedVersion', async () => {
    const store = makeFakeStore({ current: null });
    const fetchFn = vi.fn().mockResolvedValue(detail('A'));

    await autoOpenArtifact('sess-1', 'A', store.deps, fetchFn);

    expect(store.setCurrentAuto).toHaveBeenCalledOnce();
    expect(store.setCurrentAuto).toHaveBeenCalledWith(detail('A'));
    expect(store.refreshCurrent).not.toHaveBeenCalled();
    expect(store.setVersions).toHaveBeenCalledOnce();
    expect(store.setSelectedVersion).toHaveBeenCalledWith(null);
    expect(store.snapshot().autoSelected).toBe(true);
  });

  test('out-of-order cross-artifact: late A dropped after fast B applied', async () => {
    // Scenario: agent fires update_artifact(A) then update_artifact(B).
    // A's fetch is slow, B's is fast. B resolves first → applies B.
    // A resolves later → MUST be dropped (would otherwise overwrite B).
    const store = makeFakeStore({ current: null });
    const dA = deferred<ArtifactDetail>();
    const dB = deferred<ArtifactDetail>();
    const fetchFn = vi
      .fn()
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const pA = autoOpenArtifact('sess-1', 'A', store.deps, fetchFn);
    const pB = autoOpenArtifact('sess-1', 'B', store.deps, fetchFn);

    // B resolves first
    dB.resolve(detail('B'));
    await pB;
    expect(store.setCurrentAuto).toHaveBeenLastCalledWith(detail('B'));
    expect(store.snapshot().current?.id).toBe('B');

    // A resolves later — must NOT write
    dA.resolve(detail('A'));
    await pA;
    expect(store.setCurrentAuto).toHaveBeenCalledTimes(1);  // still just the B call
    expect(store.snapshot().current?.id).toBe('B');
  });

  test('stream-end revert invalidates in-flight fetch', async () => {
    // Scenario: open(A) fires mid-stream. Stream completes and
    // refreshAfterComplete bumps the gen to invalidate everything in
    // flight before clearing the panel. A's late resolve must be dropped.
    const store = makeFakeStore({ current: null });
    const d = deferred<ArtifactDetail>();
    const fetchFn = vi.fn().mockReturnValue(d.promise);

    const p = autoOpenArtifact('sess-1', 'A', store.deps, fetchFn);

    // Simulate refreshAfterComplete's bump
    bumpArtifactFetchGen();

    d.resolve(detail('A'));
    await p;

    expect(store.setCurrentAuto).not.toHaveBeenCalled();
    expect(store.refreshCurrent).not.toHaveBeenCalled();
  });

  test('same-id refresh preserves ownership (autoSelected stays false)', async () => {
    // User actively picked A → autoSelected=false. Stream then updates A.
    // The refresh must NOT flip autoSelected back to true, otherwise
    // refreshAfterComplete would later send the user to the list.
    const store = makeFakeStore({
      current: detail('A', 1),
      autoSelected: false,
    });
    const fetchFn = vi.fn().mockResolvedValue(detail('A', 2));

    await autoOpenArtifact('sess-1', 'A', store.deps, fetchFn);

    expect(store.refreshCurrent).toHaveBeenCalledOnce();
    expect(store.refreshCurrent).toHaveBeenCalledWith(detail('A', 2));
    expect(store.setCurrentAuto).not.toHaveBeenCalled();
    expect(store.snapshot().autoSelected).toBe(false);  // critical: stays false
    expect(store.snapshot().current?.current_version).toBe(2);
  });

  test('same-id agent-chained refresh preserves ownership (autoSelected stays true)', async () => {
    // Agent opened A and is now updating it again. autoSelected should
    // stay true (so refreshAfterComplete reverts to list at end).
    const store = makeFakeStore({
      current: detail('A', 1),
      autoSelected: true,
    });
    const fetchFn = vi.fn().mockResolvedValue(detail('A', 2));

    await autoOpenArtifact('sess-1', 'A', store.deps, fetchFn);

    expect(store.refreshCurrent).toHaveBeenCalledOnce();
    expect(store.setCurrentAuto).not.toHaveBeenCalled();
    expect(store.snapshot().autoSelected).toBe(true);
  });

  test('same-id out-of-order: older version dropped', async () => {
    // Already showing A v3 (agent or user). A v2 fetch resolves late.
    // Must not write.
    const store = makeFakeStore({
      current: detail('A', 3),
      autoSelected: true,
    });
    const fetchFn = vi.fn().mockResolvedValue(detail('A', 2));

    await autoOpenArtifact('sess-1', 'A', store.deps, fetchFn);

    expect(store.refreshCurrent).not.toHaveBeenCalled();
    expect(store.setCurrentAuto).not.toHaveBeenCalled();
  });

  test('cross-id with autoSelected=true (agent chain): switches', async () => {
    // Agent opened A (autoSelected=true), now updating B. Panel should
    // follow to B — this is the "follow latest agent edit" behavior.
    const store = makeFakeStore({
      current: detail('A'),
      autoSelected: true,
    });
    const fetchFn = vi.fn().mockResolvedValue(detail('B'));

    await autoOpenArtifact('sess-1', 'B', store.deps, fetchFn);

    expect(store.setCurrentAuto).toHaveBeenCalledOnce();
    expect(store.setCurrentAuto).toHaveBeenCalledWith(detail('B'));
    expect(store.snapshot().current?.id).toBe('B');
  });

  test('cross-id with autoSelected=false (user pick): refuses', async () => {
    // User picked A (autoSelected=false). Agent now updates B. Panel
    // must NOT switch — that would yank the user off A.
    const store = makeFakeStore({
      current: detail('A'),
      autoSelected: false,
    });
    const fetchFn = vi.fn().mockResolvedValue(detail('B'));

    await autoOpenArtifact('sess-1', 'B', store.deps, fetchFn);

    expect(store.setCurrentAuto).not.toHaveBeenCalled();
    expect(store.refreshCurrent).not.toHaveBeenCalled();
    expect(store.snapshot().current?.id).toBe('A');
    expect(store.snapshot().autoSelected).toBe(false);
  });

  test('fetch error: swallowed silently, no setters called', async () => {
    const store = makeFakeStore({ current: null });
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));

    await expect(
      autoOpenArtifact('sess-1', 'A', store.deps, fetchFn)
    ).resolves.toBeUndefined();

    expect(store.setCurrentAuto).not.toHaveBeenCalled();
    expect(store.refreshCurrent).not.toHaveBeenCalled();
    expect(store.setVersions).not.toHaveBeenCalled();
  });

  test('three concurrent same-stream opens: only latest wins', async () => {
    // Agent updates A → B → C in rapid succession. Resolution order: C, A, B.
    // Only C should land — A and B are stale fetches by the time they resolve.
    const store = makeFakeStore({ current: null });
    const dA = deferred<ArtifactDetail>();
    const dB = deferred<ArtifactDetail>();
    const dC = deferred<ArtifactDetail>();
    const fetchFn = vi
      .fn()
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise)
      .mockReturnValueOnce(dC.promise);

    const pA = autoOpenArtifact('sess-1', 'A', store.deps, fetchFn);
    const pB = autoOpenArtifact('sess-1', 'B', store.deps, fetchFn);
    const pC = autoOpenArtifact('sess-1', 'C', store.deps, fetchFn);

    dC.resolve(detail('C'));
    await pC;
    dA.resolve(detail('A'));
    await pA;
    dB.resolve(detail('B'));
    await pB;

    expect(store.setCurrentAuto).toHaveBeenCalledTimes(1);
    expect(store.setCurrentAuto).toHaveBeenCalledWith(detail('C'));
    expect(store.snapshot().current?.id).toBe('C');
  });
});

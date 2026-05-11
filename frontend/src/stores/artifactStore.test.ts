import { describe, test, expect, beforeEach } from 'vitest';
import { useArtifactStore } from './artifactStore';
import type { ArtifactDetail } from '@/types';

function detail(content_type: string): ArtifactDetail {
  return {
    id: 'art-1',
    session_id: 'sess-1',
    content_type,
    title: 'x',
    content: 'body',
    current_version: 1,
    source: null,
  } as ArtifactDetail;
}

describe('artifactStore.setCurrent → defaultViewMode', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('text/markdown → preview mode', () => {
    useArtifactStore.getState().setCurrent(detail('text/markdown'));
    expect(useArtifactStore.getState().viewMode).toBe('preview');
  });

  test('non-markdown content type → source mode', () => {
    useArtifactStore.getState().setCurrent(detail('application/json'));
    expect(useArtifactStore.getState().viewMode).toBe('source');
  });

  test('text/plain → source mode', () => {
    useArtifactStore.getState().setCurrent(detail('text/plain'));
    expect(useArtifactStore.getState().viewMode).toBe('source');
  });

  test('null artifact → preview mode (default fallback)', () => {
    // Pre-set to source so we can detect the change
    useArtifactStore.setState({ viewMode: 'source' });
    useArtifactStore.getState().setCurrent(null);
    expect(useArtifactStore.getState().viewMode).toBe('preview');
  });
});

describe('artifactStore.autoSelected provenance flag', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('initial state → false', () => {
    expect(useArtifactStore.getState().autoSelected).toBe(false);
  });

  test('setCurrent (user pick path) → autoSelected stays false', () => {
    useArtifactStore.getState().setCurrent(detail('text/markdown'));
    expect(useArtifactStore.getState().autoSelected).toBe(false);
  });

  test('setCurrentAuto → autoSelected becomes true', () => {
    useArtifactStore.getState().setCurrentAuto(detail('text/markdown'));
    expect(useArtifactStore.getState().autoSelected).toBe(true);
    expect(useArtifactStore.getState().current?.id).toBe('art-1');
  });

  test('setCurrent after setCurrentAuto → flag reverts to false (user reclaims)', () => {
    useArtifactStore.getState().setCurrentAuto(detail('text/markdown'));
    useArtifactStore.getState().setCurrent(detail('text/plain'));
    expect(useArtifactStore.getState().autoSelected).toBe(false);
  });

  test('setCurrent(null) → flag cleared', () => {
    useArtifactStore.getState().setCurrentAuto(detail('text/markdown'));
    useArtifactStore.getState().setCurrent(null);
    expect(useArtifactStore.getState().autoSelected).toBe(false);
    expect(useArtifactStore.getState().current).toBe(null);
  });

  test('reset → flag cleared', () => {
    useArtifactStore.getState().setCurrentAuto(detail('text/markdown'));
    useArtifactStore.getState().reset();
    expect(useArtifactStore.getState().autoSelected).toBe(false);
  });
});

describe('artifactStore.refreshCurrent', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('same-id refresh: updates current without touching autoSelected', () => {
    const v1 = { ...detail('text/markdown'), current_version: 1 } as ArtifactDetail;
    const v2 = { ...detail('text/markdown'), current_version: 2 } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(v1);  // user pick → autoSelected=false
    expect(useArtifactStore.getState().autoSelected).toBe(false);

    useArtifactStore.getState().refreshCurrent(v2);

    expect(useArtifactStore.getState().current?.current_version).toBe(2);
    expect(useArtifactStore.getState().autoSelected).toBe(false);  // preserved
  });

  test('same-id refresh: does not reset viewMode', () => {
    const v1 = { ...detail('text/markdown'), current_version: 1 } as ArtifactDetail;
    const v2 = { ...detail('text/markdown'), current_version: 2 } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(v1);
    useArtifactStore.getState().setViewMode('diff');  // user-chosen mode

    useArtifactStore.getState().refreshCurrent(v2);

    expect(useArtifactStore.getState().viewMode).toBe('diff');  // preserved
  });

  test('cross-id refresh: no-op (guard against accidental misuse)', () => {
    const a = { ...detail('text/markdown'), id: 'A', current_version: 1 } as ArtifactDetail;
    const b = { ...detail('text/markdown'), id: 'B', current_version: 1 } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(a);

    useArtifactStore.getState().refreshCurrent(b);

    expect(useArtifactStore.getState().current?.id).toBe('A');  // unchanged
  });

  test('refresh when current is null: no-op', () => {
    const a = { ...detail('text/markdown'), id: 'A' } as ArtifactDetail;

    useArtifactStore.getState().refreshCurrent(a);

    expect(useArtifactStore.getState().current).toBe(null);
  });
});

describe('artifactStore.addPendingUpdate', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('first add → identifier appended', () => {
    useArtifactStore.getState().addPendingUpdate('art-A');
    expect(useArtifactStore.getState().pendingUpdates).toEqual(['art-A']);
  });

  test('duplicate identifier → not added again (dedup)', () => {
    useArtifactStore.getState().addPendingUpdate('art-A');
    useArtifactStore.getState().addPendingUpdate('art-A');
    useArtifactStore.getState().addPendingUpdate('art-A');
    expect(useArtifactStore.getState().pendingUpdates).toEqual(['art-A']);
  });

  test('different identifiers → all added in order', () => {
    useArtifactStore.getState().addPendingUpdate('a');
    useArtifactStore.getState().addPendingUpdate('b');
    useArtifactStore.getState().addPendingUpdate('a');  // dup
    useArtifactStore.getState().addPendingUpdate('c');
    expect(useArtifactStore.getState().pendingUpdates).toEqual(['a', 'b', 'c']);
  });

  test('clearPendingUpdates resets to empty', () => {
    useArtifactStore.getState().addPendingUpdate('a');
    useArtifactStore.getState().addPendingUpdate('b');
    useArtifactStore.getState().clearPendingUpdates();
    expect(useArtifactStore.getState().pendingUpdates).toEqual([]);
  });
});

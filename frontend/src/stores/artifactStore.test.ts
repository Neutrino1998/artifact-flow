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

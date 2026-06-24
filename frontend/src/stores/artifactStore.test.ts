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

describe('artifactStore live reduce (ARTIFACT_* events)', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('CREATED stores live content, upserts list, auto-opens (source=agent)', () => {
    const s = useArtifactStore.getState();
    s.setSessionId('sess-1');
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'hello',
    });
    const st = useArtifactStore.getState();
    expect(st.liveContent['doc'].content).toBe('hello');
    expect(st.artifacts.some((a) => a.id === 'doc')).toBe(true);
    expect(st.current?.id).toBe('doc');
    expect(st.autoSelected).toBe(true);
    expect(st.pendingUpdates).toContain('doc');
  });

  test('CREATED with source=tool now auto-opens (visible live)', () => {
    const s = useArtifactStore.getState();
    s.setSessionId('sess-1');
    s.applyArtifactCreated({
      id: 'tool_out', title: 'Output', content_type: 'text/plain',
      source: 'tool', current_version: 1, content: 'log',
    });
    const st = useArtifactStore.getState();
    expect(st.current?.id).toBe('tool_out');  // tool output no longer hidden behind the list
    expect(st.autoSelected).toBe(true);
    expect(st.artifacts.some((a) => a.id === 'tool_out')).toBe(true);
  });

  test('CREATED does NOT steal from a user-selected artifact', () => {
    const s = useArtifactStore.getState();
    s.setSessionId('sess-1');
    // user actively picks an artifact: setCurrent marks autoSelected=false
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'hello',
    });
    s.setCurrent(useArtifactStore.getState().current!);
    expect(useArtifactStore.getState().autoSelected).toBe(false);
    // a tool artifact arrives mid-turn → listed, but must NOT grab the panel
    useArtifactStore.getState().applyArtifactCreated({
      id: 'tool_out', title: 'Output', content_type: 'text/plain',
      source: 'tool', current_version: 1, content: 'log',
    });
    const st = useArtifactStore.getState();
    expect(st.current?.id).toBe('doc');  // user selection untouched
    expect(st.artifacts.some((a) => a.id === 'tool_out')).toBe(true);  // still listed
  });

  test('UPDATED span delta applies onto the live base', () => {
    const s = useArtifactStore.getState();
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'alpha beta gamma',
    });
    useArtifactStore.getState().applyArtifactUpdated({
      id: 'doc', current_version: 2,
      delta: { offset: 6, deleted_len: 4, inserted_text: 'BETA' },
    });
    const st = useArtifactStore.getState();
    expect(st.liveContent['doc'].content).toBe('alpha BETA gamma');
    expect(st.current?.content).toBe('alpha BETA gamma');
    expect(st.current?.current_version).toBe(2);
  });

  test('UPDATED full content (rewrite) replaces base', () => {
    const s = useArtifactStore.getState();
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'old',
    });
    useArtifactStore.getState().applyArtifactUpdated({
      id: 'doc', current_version: 2, content: 'brand new',
    });
    expect(useArtifactStore.getState().liveContent['doc'].content).toBe('brand new');
  });

  test('selectFromLive returns true and opens user-picked (not auto)', () => {
    const s = useArtifactStore.getState();
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'tool', current_version: 1, content: 'body',  // tool → not auto-opened
    });
    const handled = useArtifactStore.getState().selectFromLive('doc');
    expect(handled).toBe(true);
    expect(useArtifactStore.getState().current?.id).toBe('doc');
    expect(useArtifactStore.getState().autoSelected).toBe(false);
  });

  test('selectFromLive returns false when no live entry', () => {
    expect(useArtifactStore.getState().selectFromLive('missing')).toBe(false);
  });

  test('clearLiveContent empties the map', () => {
    const s = useArtifactStore.getState();
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'x',
    });
    useArtifactStore.getState().clearLiveContent();
    expect(useArtifactStore.getState().liveContent).toEqual({});
  });
});

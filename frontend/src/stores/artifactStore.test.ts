import { describe, test, expect, beforeEach } from 'vitest';
import { useArtifactStore } from './artifactStore';
import type { ArtifactDetail, VersionDetail, VersionSummary } from '@/types';

function detail(content_type: string): ArtifactDetail {
  return {
    id: 'art-1',
    session_id: 'sess-1',
    content_type,
    title: 'x',
    content: 'body',
    current_version: 1,
    source: null,
    original_filename: null,
    has_blob: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    versions: [],
  };
}

function versionSummary(version: number): VersionSummary {
  return {
    version,
    update_type: version === 1 ? 'create' : 'update',
    created_at: `2026-01-0${version}T00:00:00Z`,
  };
}

function versionDetail(version: number): VersionDetail {
  return { ...versionSummary(version), content: `version ${version}` };
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

describe('artifactStore file tabs', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('opening details appends IDs once and preserves their order', () => {
    const first = { ...detail('text/markdown'), id: 'A' } as ArtifactDetail;
    const second = { ...detail('text/plain'), id: 'B' } as ArtifactDetail;

    useArtifactStore.getState().setCurrent(first);
    useArtifactStore.getState().setCurrent(second);
    useArtifactStore.getState().setCurrent(first);

    expect(useArtifactStore.getState().openArtifactIds).toEqual(['A', 'B']);
  });

  test('returning to the file tree keeps open tabs', () => {
    useArtifactStore.getState().setCurrent(detail('text/markdown'));
    useArtifactStore.getState().setCurrent(null);

    expect(useArtifactStore.getState().current).toBeNull();
    expect(useArtifactStore.getState().openArtifactIds).toEqual(['art-1']);
  });

  test('closing the active tab clears its detail and view-scoped state', () => {
    useArtifactStore.getState().setCurrent(detail('text/markdown'));
    useArtifactStore.getState().setViewMode('diff');
    useArtifactStore.getState().closeArtifactTab('art-1');

    const state = useArtifactStore.getState();
    expect(state.openArtifactIds).toEqual([]);
    expect(state.current).toBeNull();
    expect(state.viewMode).toBe('preview');
  });

  test('closing an inactive tab leaves the active detail alone', () => {
    const first = { ...detail('text/markdown'), id: 'A' } as ArtifactDetail;
    const second = { ...detail('text/plain'), id: 'B' } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(first);
    useArtifactStore.getState().setCurrent(second);

    useArtifactStore.getState().closeArtifactTab('A');

    expect(useArtifactStore.getState().openArtifactIds).toEqual(['B']);
    expect(useArtifactStore.getState().current?.id).toBe('B');
  });

  test('reset clears tabs with the conversation-scoped artifact state', () => {
    useArtifactStore.getState().setCurrent(detail('text/markdown'));
    useArtifactStore.getState().reset();
    expect(useArtifactStore.getState().openArtifactIds).toEqual([]);
  });
});

describe('artifactStore.refreshCurrent', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('same-id refresh atomically updates detail and versions without touching autoSelected', () => {
    const v1 = {
      ...detail('text/markdown'),
      current_version: 1,
      versions: [versionSummary(1)],
    } as ArtifactDetail;
    const v2 = {
      ...detail('text/markdown'),
      current_version: 2,
      versions: [versionSummary(1), versionSummary(2)],
    } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(v1);  // user pick → autoSelected=false
    useArtifactStore.getState().setVersions(v1.versions);
    useArtifactStore.getState().setSelectedVersion(versionDetail(1));
    expect(useArtifactStore.getState().autoSelected).toBe(false);

    useArtifactStore.getState().refreshCurrent(v2, 'version 1');

    expect(useArtifactStore.getState().current?.current_version).toBe(2);
    expect(useArtifactStore.getState().versions).toEqual(v2.versions);
    expect(useArtifactStore.getState().selectedVersion).toBeNull();
    expect(useArtifactStore.getState().diffBaseContent).toBe('version 1');
    expect(useArtifactStore.getState().autoSelected).toBe(false);  // preserved
  });

  test('resolved DB diff base preserves diff viewMode', () => {
    const v1 = { ...detail('text/markdown'), current_version: 1 } as ArtifactDetail;
    const v2 = { ...detail('text/markdown'), current_version: 2 } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(v1);
    useArtifactStore.getState().setViewMode('diff');  // user-chosen mode

    useArtifactStore.getState().refreshCurrent(v2, 'persisted v1');

    expect(useArtifactStore.getState().viewMode).toBe('diff');  // preserved
    expect(useArtifactStore.getState().diffBaseContent).toBe('persisted v1');
  });

  test('failed DB diff-base request exits diff instead of showing a stale comparison', () => {
    const v1 = { ...detail('text/markdown'), current_version: 1 } as ArtifactDetail;
    const v2 = { ...detail('text/markdown'), current_version: 2 } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(v1);
    useArtifactStore.getState().setDiffBaseContent('older stale base');
    useArtifactStore.getState().setViewMode('diff');

    useArtifactStore.getState().refreshCurrent(v2, undefined);

    expect(useArtifactStore.getState().current?.current_version).toBe(2);
    expect(useArtifactStore.getState().diffBaseContent).toBeNull();
    expect(useArtifactStore.getState().viewMode).toBe('preview');
  });

  test('late refresh after a tab switch does not pollute the new current artifact', () => {
    const a = { ...detail('text/markdown'), id: 'A', current_version: 1 } as ArtifactDetail;
    const b = { ...detail('text/markdown'), id: 'B', current_version: 1 } as ArtifactDetail;
    const bVersions = [versionSummary(1)];
    const bSelected = versionDetail(1);
    useArtifactStore.getState().setCurrent(a);
    useArtifactStore.getState().setCurrent(b);
    useArtifactStore.getState().setVersions(bVersions);
    useArtifactStore.getState().setSelectedVersion(bSelected);

    useArtifactStore.getState().refreshCurrent(
      {
        ...a,
        current_version: 2,
        versions: [versionSummary(1), versionSummary(2)],
      },
      'A base',
    );

    expect(useArtifactStore.getState().current?.id).toBe('B');
    expect(useArtifactStore.getState().versions).toEqual(bVersions);
    expect(useArtifactStore.getState().selectedVersion).toEqual(bSelected);
  });

  test('late refresh after closing the active tab does not reopen it', () => {
    const a = { ...detail('text/markdown'), id: 'A', current_version: 1 } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(a);
    useArtifactStore.getState().setVersions([versionSummary(1)]);
    useArtifactStore.getState().setSelectedVersion(versionDetail(1));
    useArtifactStore.getState().closeArtifactTab('A');

    useArtifactStore.getState().refreshCurrent(
      {
        ...a,
        current_version: 2,
        versions: [versionSummary(1), versionSummary(2)],
      },
      'A base',
    );

    const state = useArtifactStore.getState();
    expect(state.current).toBeNull();
    expect(state.openArtifactIds).toEqual([]);
    expect(state.versions).toEqual([]);
    expect(state.selectedVersion).toBeNull();
  });

  test('refresh when current is null: no-op', () => {
    const a = { ...detail('text/markdown'), id: 'A' } as ArtifactDetail;

    useArtifactStore.getState().refreshCurrent(a, 'A base');

    expect(useArtifactStore.getState().current).toBe(null);
  });

  test('flush-error reconciliation rolls optimistic live content back to authoritative DB', () => {
    const liveV3 = {
      ...detail('text/markdown'),
      content: 'live v3',
      current_version: 3,
      versions: [],
    } as ArtifactDetail;
    const staleV2 = {
      ...detail('text/markdown'),
      content: 'persisted DB v2',
      current_version: 2,
      versions: [versionSummary(1), versionSummary(2)],
    } as ArtifactDetail;
    useArtifactStore.getState().setCurrent(liveV3);
    useArtifactStore.getState().setVersions([]);
    useArtifactStore.getState().setDiffBaseContent('live base');

    useArtifactStore.getState().refreshCurrent(staleV2, 'persisted v1');

    const state = useArtifactStore.getState();
    expect(state.current?.content).toBe('persisted DB v2');
    expect(state.current?.current_version).toBe(2);
    expect(state.versions).toEqual(staleV2.versions);
    expect(state.diffBaseContent).toBe('persisted v1');
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

});

describe('artifactStore.finishLiveTurn', () => {
  beforeEach(() => useArtifactStore.getState().reset());

  test('clears live-only state while preserving a user-selected current file', () => {
    const store = useArtifactStore.getState();
    store.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'agent', current_version: 2, content: 'live content',
    });
    store.selectFromLive('doc');
    store.setCurrentLoading(true);
    store.setLocalPreviews([
      new File(['image'], 'old-preview.png', { type: 'image/png' }),
    ]);

    useArtifactStore.getState().finishLiveTurn();

    const state = useArtifactStore.getState();
    expect(state.current?.id).toBe('doc');
    expect(state.autoSelected).toBe(false);
    expect(state.currentLoading).toBe(false);
    expect(state.pendingUpdates).toEqual([]);
    expect(state.liveContent).toEqual({});
    expect(state.localPreviews).toEqual({});
  });

  test('returns an agent-auto-opened file to the list', () => {
    useArtifactStore.getState().applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'live content',
    });

    useArtifactStore.getState().finishLiveTurn();

    const state = useArtifactStore.getState();
    expect(state.current).toBeNull();
    expect(state.autoSelected).toBe(false);
    expect(state.openArtifactIds).toEqual(['doc']);
  });

  test('a new turn appends markers and live content onto a clean state', () => {
    useArtifactStore.getState().applyArtifactCreated({
      id: 'old-doc', title: 'Old', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'old turn',
    });
    useArtifactStore.getState().finishLiveTurn();

    useArtifactStore.getState().applyArtifactCreated({
      id: 'new-doc', title: 'New', content_type: 'text/markdown',
      source: 'agent', current_version: 1, content: 'new turn',
    });

    const state = useArtifactStore.getState();
    expect(state.pendingUpdates).toEqual(['new-doc']);
    expect(Object.keys(state.liveContent)).toEqual(['new-doc']);
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

  test('UPDATED blob overwrite with NO live base renders binary, not empty markdown', () => {
    // Cross-turn `mount → edit → persist artifact_id=…`: the artifact came from a
    // prior turn, so its first event this turn is the blob ARTIFACT_UPDATED. It must
    // synthesize a binary live entry from the event's has_blob/content_type — not an
    // empty text/markdown view (reviewer #1 regression).
    useArtifactStore.getState().applyArtifactUpdated({
      id: 'pkg.zip', current_version: 1, content: '',
      has_blob: true, blob_size: 2048, content_type: 'application/zip',
    });
    const st = useArtifactStore.getState();
    expect(st.liveContent['pkg.zip'].hasBlob).toBe(true);
    expect(st.liveContent['pkg.zip'].contentType).toBe('application/zip');
  });

  test('UPDATED without blob fields keeps base hasBlob/contentType (text path unchanged)', () => {
    const s = useArtifactStore.getState();
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/plain',
      source: 'agent', current_version: 1, content: 'old',
    });
    useArtifactStore.getState().applyArtifactUpdated({
      id: 'doc', current_version: 2, content: 'new',
    });
    const st = useArtifactStore.getState();
    expect(st.liveContent['doc'].hasBlob).toBe(false);
    expect(st.liveContent['doc'].contentType).toBe('text/plain');
  });

  test('selectFromLive returns true and opens user-picked (not auto)', () => {
    const s = useArtifactStore.getState();
    s.applyArtifactCreated({
      id: 'doc', title: 'Doc', content_type: 'text/markdown',
      source: 'tool', current_version: 1, content: 'body',
    });
    // selectFromLive flips an auto-opened artifact into a user pick (autoSelected=false),
    // independent of source — that override is what this asserts.
    const handled = useArtifactStore.getState().selectFromLive('doc');
    expect(handled).toBe(true);
    expect(useArtifactStore.getState().current?.id).toBe('doc');
    expect(useArtifactStore.getState().autoSelected).toBe(false);
  });

  test('selectFromLive returns false when no live entry', () => {
    expect(useArtifactStore.getState().selectFromLive('missing')).toBe(false);
  });

});

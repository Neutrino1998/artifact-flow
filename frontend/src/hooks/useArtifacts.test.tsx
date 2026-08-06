import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ArtifactDetail, ArtifactSummary, ConversationDetail } from '@/types';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';
import { _resetArtifactDetailGenForTests } from '@/lib/artifactDetailGen';
import { _resetGenerationForTests } from '@/lib/refreshArtifactList';
import { useArtifacts } from './useArtifacts';

const apiMocks = vi.hoisted(() => ({
  getArtifact: vi.fn(),
  listArtifacts: vi.fn(),
}));

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    getArtifact: apiMocks.getArtifact,
    listArtifacts: apiMocks.listArtifacts,
  };
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function summary(id: string): ArtifactSummary {
  return {
    id,
    content_type: 'text/markdown',
    title: `Document ${id}`,
    current_version: 1,
    source: 'agent',
    original_filename: null,
    has_blob: false,
    created_at: '2026-08-06T00:00:00',
    updated_at: '2026-08-06T00:00:00',
  } as ArtifactSummary;
}

function detail(id: string): ArtifactDetail {
  return {
    ...summary(id),
    session_id: 'session-1',
    content: 'body',
    versions: [],
  } as ArtifactDetail;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('useArtifacts selection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let selectArtifact: ReturnType<typeof useArtifacts>['selectArtifact'];
  let loadArtifacts: ReturnType<typeof useArtifacts>['loadArtifacts'];

  function Harness() {
    ({ selectArtifact, loadArtifacts } = useArtifacts());
    return null;
  }

  beforeEach(async () => {
    apiMocks.getArtifact.mockReset();
    apiMocks.listArtifacts.mockReset();
    _resetArtifactDetailGenForTests();
    _resetGenerationForTests();
    useArtifactStore.getState().reset();
    useConversationStore.getState().reset();
    useStreamStore.getState().reset();
    useUIStore.setState(INITIAL_UI_STATE);
    useConversationStore.getState().setCurrent({
      id: 'conversation-1',
      session_id: 'session-1',
      title: 'Conversation',
      active_branch: null,
      messages: [],
      created_at: '2026-08-06T00:00:00',
      updated_at: '2026-08-06T00:00:00',
    } as ConversationDetail);
    useArtifactStore.getState().reconcileArtifactsFromDb([summary('A'), summary('B')]);
    useArtifactStore.getState().setCurrent(detail('A'));
    useArtifactStore.getState().setCurrent(detail('B'));
    useArtifactStore.getState().setCurrent(detail('A'));

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root.render(<Harness />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useArtifactStore.getState().reset();
    useConversationStore.getState().reset();
    useStreamStore.getState().reset();
  });

  it('keeps a live-only file when the panel loads a lagging DB list', async () => {
    useArtifactStore.getState().reset();
    useArtifactStore.getState().applyArtifactCreated({
      id: 'live-only',
      title: 'Live only',
      content_type: 'text/markdown',
      source: 'agent',
      current_version: 1,
      content: 'not flushed',
    });
    useStreamStore.getState().startStream('/stream', 'message-1', 'session-1');
    apiMocks.listArtifacts.mockResolvedValue({
      session_id: 'session-1',
      artifacts: [summary('persisted')],
    });

    await act(async () => loadArtifacts());

    const state = useArtifactStore.getState();
    expect(state.artifacts.map((artifact) => artifact.id)).toEqual(['persisted', 'live-only']);
    expect(state.openArtifactIds).toEqual(['live-only']);
    expect(state.current?.id).toBe('live-only');
  });

  it('does not reopen an existing tab closed before its delayed detail arrives', async () => {
    const request = deferred<ArtifactDetail>();
    apiMocks.getArtifact.mockReturnValueOnce(request.promise);

    let selection!: Promise<void>;
    await act(async () => {
      selection = selectArtifact('B');
      await Promise.resolve();
    });
    expect(useArtifactStore.getState().currentLoading).toBe(true);

    act(() => useArtifactStore.getState().closeArtifactTab('B'));
    expect(useArtifactStore.getState().openArtifactIds).toEqual(['A']);

    await act(async () => {
      request.resolve(detail('B'));
      await selection;
    });

    const state = useArtifactStore.getState();
    expect(state.current?.id).toBe('A');
    expect(state.openArtifactIds).toEqual(['A']);
    expect(state.currentLoading).toBe(false);
  });
});

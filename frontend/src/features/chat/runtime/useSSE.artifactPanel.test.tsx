import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SSEHandlers } from '@/lib/sse';
import { StreamEventType, type SSEEvent } from '@/types/events';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { INITIAL_UI_STATE, useUIStore, type ActiveMode } from '@/stores/uiStore';
import { useSSE } from './useSSE';

const sseMock = vi.hoisted(() => ({
  handlers: null as SSEHandlers | null,
}));

vi.mock('@/lib/sse', () => ({
  connectSSE: vi.fn((_url: string, handlers: SSEHandlers) => {
    sseMock.handlers = handlers;
    return { lastEventId: null };
  }),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function artifactCreatedEvent(): SSEEvent {
  return {
    type: StreamEventType.ARTIFACT_CREATED,
    timestamp: '2026-08-08T00:00:00Z',
    data: {
      id: 'artifact-1',
      title: 'Live artifact',
      content_type: 'text/markdown',
      source: 'agent',
      current_version: 1,
      content: 'body',
    },
  };
}

describe('useSSE artifact panel auto-open', () => {
  let container: HTMLDivElement;
  let root: Root;
  let connect: ReturnType<typeof useSSE>['connect'];
  let disconnect: ReturnType<typeof useSSE>['disconnect'];

  function Harness() {
    ({ connect, disconnect } = useSSE());
    return null;
  }

  beforeEach(async () => {
    sseMock.handlers = null;
    useArtifactStore.getState().reset();
    useConversationStore.getState().reset();
    useStreamStore.getState().reset();
    useUIStore.setState(INITIAL_UI_STATE);

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root.render(<Harness />));
    act(() => connect('/stream', 'conversation-1', 'message-1'));
  });

  afterEach(() => {
    act(() => {
      disconnect();
      root.unmount();
    });
    container.remove();
  });

  it('auto-opens for a live artifact in ordinary chat mode', () => {
    act(() => sseMock.handlers?.onEvent(artifactCreatedEvent()));

    expect(useArtifactStore.getState().artifacts).toHaveLength(1);
    expect(useUIStore.getState().artifactPanelVisible).toBe(true);
  });

  it.each(['skills', 'instances'] satisfies ActiveMode[])(
    'does not leave latent visibility when an artifact arrives during %s',
    (mode) => {
      act(() => useUIStore.getState().setActiveMode(mode));
      act(() => sseMock.handlers?.onEvent(artifactCreatedEvent()));

      // The live artifact still updates its own durable UI store; only the
      // automatic panel-open request is suppressed by the takeover mode.
      expect(useArtifactStore.getState().artifacts).toHaveLength(1);
      expect(useUIStore.getState().artifactPanelVisible).toBe(false);

      act(() => useUIStore.getState().setActiveMode('none'));
      expect(useUIStore.getState().artifactPanelVisible).toBe(false);
    },
  );
});

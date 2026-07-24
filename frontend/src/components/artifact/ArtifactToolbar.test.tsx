import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import ArtifactToolbar from './ArtifactToolbar';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStreamStore } from '@/stores/streamStore';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ArtifactToolbar', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useArtifactStore.getState().reset();
    useStreamStore.setState({ isStreaming: false });
    useArtifactStore.getState().setCurrent({
      id: 'artifact-1',
      session_id: 'session-1',
      content_type: 'text/plain',
      title: 'Document',
      content: 'content',
      current_version: 1,
      source: null,
      original_filename: null,
      has_blob: false,
      created_at: '2026-07-23T00:00:00',
      updated_at: '2026-07-23T00:00:00',
      versions: [],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useArtifactStore.getState().reset();
    useStreamStore.setState({ isStreaming: false });
  });

  it('keeps the mobile back-to-list control square and restores its compact size at sm', async () => {
    await act(async () => {
      root.render(<ArtifactToolbar />);
    });

    const back = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Back to artifact list"]',
    );
    expect(back).not.toBeNull();
    expect(back?.className).toContain('w-11');
    expect(back?.className).toContain('h-11');
    expect(back?.className).toContain('sm:w-7');
    expect(back?.className).toContain('sm:h-7');

    await act(async () => back?.click());
    expect(useArtifactStore.getState().current).toBeNull();
  });
});

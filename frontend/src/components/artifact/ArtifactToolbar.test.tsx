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

  it('keeps the mobile file-tree control square and returns to the tree view', async () => {
    await act(async () => {
      root.render(<ArtifactToolbar />);
    });

    expect(container.querySelector('h3')?.textContent).toBe('Document');

    const fileTree = container.querySelector<HTMLButtonElement>(
      'button[aria-label="查看文件系统"]',
    );
    expect(fileTree).not.toBeNull();
    expect(fileTree?.className).toContain('w-11');
    expect(fileTree?.className).toContain('h-11');
    expect(fileTree?.className).toContain('sm:w-7');
    expect(fileTree?.className).toContain('sm:h-7');
    const browserIcon = fileTree?.querySelector<SVGElement>('svg.lucide-folders');
    expect(browserIcon).not.toBeNull();
    expect(browserIcon?.getAttribute('width')).toBe('14');
    expect(browserIcon?.getAttribute('height')).toBe('14');
    expect(Number(browserIcon?.getAttribute('stroke-width'))).toBeGreaterThan(2);

    await act(async () => fileTree?.click());
    expect(useArtifactStore.getState().current).toBeNull();
    expect(useArtifactStore.getState().openArtifactIds).toEqual(['artifact-1']);
  });
});

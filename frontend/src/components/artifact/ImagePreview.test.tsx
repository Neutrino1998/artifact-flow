import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStreamStore } from '@/stores/streamStore';
import ImagePreview from './ImagePreview';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ImagePreview', () => {
  let container: HTMLDivElement;
  let root: Root;
  const revokeObjectUrl = vi.fn();

  beforeEach(() => {
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectUrl,
    });
    useArtifactStore.setState({ liveContent: {}, localPreviews: {} });
    useStreamStore.setState({ isStreaming: false });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('opens the already-fetched image in the shared viewer without another request', async () => {
    const fetchRawObjectUrl = vi.fn().mockResolvedValue('blob:image-preview');

    await act(async () => {
      root.render(
        <ImagePreview
          sessionId="session-1"
          artifactId="image-1"
          originalFilename="chart.webp"
          fetchRawObjectUrl={fetchRawObjectUrl}
          pendingFlush={false}
          useLocalPreview={false}
        />,
      );
    });

    const inlineImage = container.querySelector<HTMLImageElement>('img');
    expect(inlineImage?.getAttribute('src')).toBe('blob:image-preview');
    expect(fetchRawObjectUrl).toHaveBeenCalledOnce();

    Object.defineProperty(inlineImage, 'naturalWidth', { configurable: true, value: 1600 });
    Object.defineProperty(inlineImage, 'naturalHeight', { configurable: true, value: 900 });
    await act(async () => inlineImage?.dispatchEvent(new Event('load')));

    const expandButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="全屏查看图片：chart.webp"]',
    );
    await act(async () => expandButton?.click());

    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]');
    const fullscreenImage = dialog?.querySelector<HTMLImageElement>('img');
    expect(dialog?.textContent).toContain('chart.webp');
    expect(fullscreenImage?.getAttribute('src')).toBe('blob:image-preview');
    expect(fetchRawObjectUrl).toHaveBeenCalledOnce();

    const closeButton = dialog?.querySelector<HTMLButtonElement>(
      'button[aria-label="Close fullscreen viewer"]',
    );
    await act(async () => closeButton?.click());
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  });
});

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import ZoomableCanvas from './ZoomableCanvas';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ZoomableCanvas', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('zooms around a fitted baseline and resets to 100%', async () => {
    await act(async () => {
      root.render(
        <div style={{ width: 800, height: 600 }}>
          <ZoomableCanvas contentWidth={1200} contentHeight={800}>
            <div>Content</div>
          </ZoomableCanvas>
        </div>,
      );
    });

    const zoomIn = container.querySelector<HTMLButtonElement>('button[aria-label="Zoom in"]');
    const zoomOut = container.querySelector<HTMLButtonElement>('button[aria-label="Zoom out"]');
    const reset = container.querySelector<HTMLButtonElement>('button[aria-label="Reset zoom"]');
    expect(reset?.textContent).toBe('100%');

    await act(async () => zoomIn?.click());
    expect(reset?.textContent).toBe('125%');

    await act(async () => zoomOut?.click());
    expect(reset?.textContent).toBe('100%');

    await act(async () => {
      zoomIn?.click();
      reset?.click();
    });
    expect(reset?.textContent).toBe('100%');
  });

  it('does not treat a rapid pair of zoom-button clicks as a canvas reset', async () => {
    await act(async () => {
      root.render(
        <div style={{ width: 800, height: 600 }}>
          <ZoomableCanvas contentWidth={1200} contentHeight={800}>
            <div>Content</div>
          </ZoomableCanvas>
        </div>,
      );
    });

    const zoomIn = container.querySelector<HTMLButtonElement>('button[aria-label="Zoom in"]');
    const reset = container.querySelector<HTMLButtonElement>('button[aria-label="Reset zoom"]');

    await act(async () => {
      zoomIn?.click();
      zoomIn?.click();
      zoomIn?.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    });

    expect(reset?.textContent).toBe('150%');
  });
});

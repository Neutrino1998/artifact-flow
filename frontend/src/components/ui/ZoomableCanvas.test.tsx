import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

  it('uses the pre-capture press target to distinguish content from background clicks', async () => {
    const onBackgroundClick = vi.fn();
    await act(async () => {
      root.render(
        <div style={{ width: 800, height: 600 }}>
          <ZoomableCanvas
            contentWidth={1200}
            contentHeight={800}
            onBackgroundClick={onBackgroundClick}
          >
            <div data-testid="content">Content</div>
          </ZoomableCanvas>
        </div>,
      );
    });

    const viewport = container.querySelector<HTMLDivElement>('[aria-label="Zoomable content"]');
    const content = container.querySelector<HTMLDivElement>('[data-testid="content"]');
    const zoomIn = container.querySelector<HTMLButtonElement>('button[aria-label="Zoom in"]');
    expect(viewport).not.toBeNull();
    expect(content).not.toBeNull();

    const setPointerCapture = vi.fn();
    Object.defineProperties(viewport, {
      setPointerCapture: { configurable: true, value: setPointerCapture },
      hasPointerCapture: { configurable: true, value: () => true },
      releasePointerCapture: { configurable: true, value: vi.fn() },
    });

    // Zoom until the content is draggable, then model the browser's captured
    // sequence: press originates on content, but pointerup and click target the
    // capturing viewport.
    await act(async () => zoomIn?.click());
    await act(async () => {
      content?.dispatchEvent(new MouseEvent('pointerdown', {
        bubbles: true,
        button: 0,
        clientX: 100,
        clientY: 100,
      }));
      viewport?.dispatchEvent(new MouseEvent('pointerup', {
        bubbles: true,
        button: 0,
        clientX: 100,
        clientY: 100,
      }));
      viewport?.dispatchEvent(new MouseEvent('click', { bubbles: true, button: 0 }));
    });

    expect(setPointerCapture).toHaveBeenCalledOnce();
    expect(onBackgroundClick).not.toHaveBeenCalled();

    // A genuine background press follows the same captured dispatch path but
    // must retain the intended click-to-close behavior.
    await act(async () => {
      viewport?.dispatchEvent(new MouseEvent('pointerdown', {
        bubbles: true,
        button: 0,
        clientX: 20,
        clientY: 20,
      }));
      viewport?.dispatchEvent(new MouseEvent('pointerup', {
        bubbles: true,
        button: 0,
        clientX: 20,
        clientY: 20,
      }));
      viewport?.dispatchEvent(new MouseEvent('click', { bubbles: true, button: 0 }));
    });

    expect(onBackgroundClick).toHaveBeenCalledOnce();
  });
});

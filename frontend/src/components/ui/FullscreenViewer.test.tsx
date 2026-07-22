import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FullscreenViewer from './FullscreenViewer';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('FullscreenViewer', () => {
  let container: HTMLDivElement;
  let trigger: HTMLButtonElement;
  let root: Root;

  beforeEach(() => {
    trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    trigger.remove();
  });

  it('portals an accessible dialog, closes on Escape and restores focus', async () => {
    const onClose = vi.fn();

    await act(async () => {
      root.render(
        <FullscreenViewer open title="Diagram" onClose={onClose}>
          <div>Viewer content</div>
        </FullscreenViewer>,
      );
    });

    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain('Diagram');
    expect(dialog?.textContent).toContain('Viewer content');
    expect(document.body.style.overflow).toBe('hidden');

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).toHaveBeenCalledOnce();

    await act(async () => {
      root.render(
        <FullscreenViewer open={false} title="Diagram" onClose={onClose}>
          <div>Viewer content</div>
        </FullscreenViewer>,
      );
    });
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.style.overflow).toBe('');
    expect(document.activeElement).toBe(trigger);
  });
});

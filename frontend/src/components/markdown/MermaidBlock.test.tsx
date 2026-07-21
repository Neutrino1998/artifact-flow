import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MermaidBlock from './MermaidBlock';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { renderMermaid } = vi.hoisted(() => ({
  renderMermaid: vi.fn(),
}));

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: renderMermaid,
  },
}));

describe('MermaidBlock', () => {
  let container: HTMLDivElement;
  let root: Root;
  const writeText = vi.fn();

  beforeEach(() => {
    renderMermaid.mockResolvedValue({
      svg: '<svg viewBox="0 0 120 80" style="max-width: 120px"><text>Rendered</text></svg>',
    });
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    });

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('copies the Mermaid source beside the SVG download action', async () => {
    const code = 'flowchart LR\n  A --> B';

    await act(async () => {
      root.render(<MermaidBlock code={code} />);
    });

    const copyButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Copy Mermaid source"]',
    );
    const downloadButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Download SVG"]',
    );

    expect(copyButton).not.toBeNull();
    expect(downloadButton).not.toBeNull();

    await act(async () => {
      copyButton?.click();
    });

    expect(writeText).toHaveBeenCalledWith(code);
    expect(copyButton?.title).toBe('已复制');
  });

  it('moves the sole diagram SVG into the fullscreen viewer and restores it on close', async () => {
    await act(async () => {
      root.render(<MermaidBlock code={'flowchart LR\n  A --> B'} />);
    });

    expect(container.textContent).toContain('Rendered');
    const expandButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Expand Mermaid diagram"]',
    );

    await act(async () => expandButton?.click());

    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]');
    expect(dialog?.textContent).toContain('Rendered');
    expect(container.textContent).not.toContain('Rendered');

    const closeButton = dialog?.querySelector<HTMLButtonElement>(
      'button[aria-label="Close fullscreen viewer"]',
    );
    await act(async () => closeButton?.click());

    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    expect(container.textContent).toContain('Rendered');
  });
});

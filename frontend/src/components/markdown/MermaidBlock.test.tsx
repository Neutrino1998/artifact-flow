import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MermaidBlock from './MermaidBlock';

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
      svg: '<svg style="max-width: 120px"><text>Rendered</text></svg>',
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
});

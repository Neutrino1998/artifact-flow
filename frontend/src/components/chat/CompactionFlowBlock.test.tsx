import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import type { CompactionBlock } from '@/stores/streamStore';
import CompactionFlowBlock from './CompactionFlowBlock';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('CompactionFlowBlock', () => {
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

  test('describes overflow recovery without inventing a zero-token count', async () => {
    const block: CompactionBlock = {
      kind: 'compaction',
      id: 'overflow',
      state: 'running',
      reason: 'overflow',
      timestamp: 't',
      position: 0,
    };

    await act(async () => {
      root.render(<CompactionFlowBlock block={block} />);
    });

    expect(container.textContent).toContain('recovering from context overflow…');
    expect(container.textContent).not.toContain('0 tokens');
  });
});

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { InstanceHeartbeat } from '@/lib/api';
import { InstanceCard } from './InstancePanel';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const instance: InstanceHeartbeat = {
  instance_id: 'backend-1',
  status: 'green',
  error_count: 2,
  loop_lag_ms: { p50_ms: 0.1, max_1m_ms: 1.6 },
  process: { rss_mb: 480 },
};

describe('InstanceCard', () => {
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

  test('opens the all-events drawer from the card and keeps expand local', async () => {
    const onOpenEvents = vi.fn();
    await act(async () => {
      root.render(
        <InstanceCard
          inst={instance}
          nowMs={Date.now()}
          isSelf
          onOpenEvents={onOpenEvents}
        />,
      );
    });

    const card = container.querySelector<HTMLElement>(
      '[aria-label="查看 backend-1 全部实例事件"]',
    );
    await act(async () => card?.click());
    expect(onOpenEvents).toHaveBeenCalledWith(instance);

    onOpenEvents.mockClear();
    const expand = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '展开详情',
    );
    await act(async () => expand?.click());
    expect(onOpenEvents).not.toHaveBeenCalled();
    expect(container.textContent).toContain('CPU:');
  });
});

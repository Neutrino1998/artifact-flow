import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { InstanceHeartbeat } from '@/lib/api';
import InstanceEventDrawer from './InstanceEventDrawer';

const apiMocks = vi.hoisted(() => ({
  getAdminInstanceEvents: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  getAdminInstanceEvents: apiMocks.getAdminInstanceEvents,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const instance: InstanceHeartbeat = {
  instance_id: 'backend-1',
  status: 'green',
  error_count: 2,
  last_error_ts: '2026-07-30T07:07:00',
  last_wedge: { ts: '2026-07-30T07:08:22', lag_ms: 5000, wedged: true },
};

describe('InstanceEventDrawer', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    apiMocks.getAdminInstanceEvents.mockReset();
    apiMocks.getAdminInstanceEvents.mockResolvedValue({
      instance_id: 'backend-1',
      sources: {
        error_log: { available: true, truncated: false },
        loop_lag: { available: true, truncated: false },
      },
      events: [
        {
          id: 'error-1',
          type: 'error',
          source: 'runtime_log',
          severity: 'error',
          ts: '2026-07-30T07:07:00',
          summary: 'LLM call failed',
          detail: 'RuntimeError: boom',
          instance_id: 'backend-1',
          conversation_id: 'conv-1',
          message_id: 'msg-1',
        },
        {
          id: 'wedge-1',
          type: 'wedge',
          source: 'loop_lag',
          severity: 'error',
          ts: '2026-07-30T07:08:22',
          summary: 'Event loop did not respond',
          lag_ms: 5000,
          lower_bound: true,
          instance_id: 'backend-1',
          tasks: [],
          threads: [],
        },
      ],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test('filters logs, shows wedge lower bound, links conversation and closes', async () => {
    const onClose = vi.fn();
    const onOpenConversation = vi.fn();

    await act(async () => {
      root.render(
        <InstanceEventDrawer
          instance={instance}
          initialFilter="error"
          onClose={onClose}
          onOpenConversation={onOpenConversation}
        />,
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.getAdminInstanceEvents).toHaveBeenCalledWith('backend-1', 'error', 50);
    expect(container.textContent).toContain('LLM call failed');
    expect(container.textContent).not.toContain('Event loop did not respond');

    const openConversation = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('在会话监控中打开'),
    );
    await act(async () => openConversation?.click());
    expect(onOpenConversation).toHaveBeenCalledWith('conv-1');

    const wedgeFilter = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Wedge',
    );
    await act(async () => {
      wedgeFilter?.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(apiMocks.getAdminInstanceEvents).toHaveBeenLastCalledWith('backend-1', 'wedge', 50);
    expect(container.textContent).toContain('Event loop did not respond');
    expect(container.textContent).toContain('≥5000ms');
    expect(container.textContent).not.toContain('LLM call failed');

    const close = container.querySelector<HTMLButtonElement>('[aria-label="关闭实例事件详情"]');
    await act(async () => close?.click());
    expect(onClose).toHaveBeenCalledOnce();
  });
});

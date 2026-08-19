import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { InstanceHeartbeat } from '@/lib/api';
import InstanceEventDrawer, { serializeInstanceEvents } from './InstanceEventDrawer';

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
  started_at: '2026-07-30T06:00:00',
  process: { cpu_pct: 12.5, open_fds: 42 },
  db_pool: { in_use: 2, size: 10, overflow: 1 },
  redis: { used_mb: 128 },
  tasks_long_running: 3,
  data_dir_mb: 256,
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
          active_message_ids: ['msg-active-1', 'msg-active-2'],
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
    expect(container.textContent).toContain('CPU');
    expect(container.textContent).toContain('12.5%');
    expect(container.textContent).toContain('2/10 +1');
    expect(container.textContent).toContain('长跑任务');
    expect(container.textContent).toContain('256M');
    expect(container.textContent).toContain('LLM call failed');
    expect(container.textContent).not.toContain('Event loop did not respond');

    const runtimeDetails = Array.from(container.querySelectorAll('details')).find(
      (details) => details.querySelector('summary')?.textContent?.trim() === '运行详情',
    );
    expect(runtimeDetails?.open).toBe(false);
    await act(async () => runtimeDetails?.querySelector('summary')?.click());
    expect(runtimeDetails?.open).toBe(true);

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
    expect(container.textContent).toContain('active messages: msg-active-1, msg-active-2');
    expect(container.textContent).not.toContain('LLM call failed');

    const close = container.querySelector<HTMLButtonElement>('[aria-label="关闭实例事件详情"]');
    await act(async () => close?.click());
    expect(onClose).toHaveBeenCalledOnce();
  });

  test('copies one event independently from the filtered event list', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    await act(async () => {
      root.render(
        <InstanceEventDrawer
          instance={instance}
          initialFilter="all"
          onClose={vi.fn()}
          onOpenConversation={vi.fn()}
        />,
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const copyButtons = container.querySelectorAll<HTMLButtonElement>('[aria-label="复制该事件诊断"]');
    expect(copyButtons).toHaveLength(2);
    expect(container.textContent).toContain('复制当前列表');
    expect(copyButtons[0].parentElement?.classList).toContain('items-center');
    expect(copyButtons[0].parentElement?.querySelector('time')?.classList).toContain('leading-5');
    expect(copyButtons[0].classList).toContain('h-5');
    expect(copyButtons[0].classList).toContain('w-5');

    await act(async () => {
      copyButtons[1].click();
      await Promise.resolve();
    });

    const copiedEvent = apiMocks.getAdminInstanceEvents.mock.results[0].value;
    const response = await copiedEvent;
    expect(writeText).toHaveBeenCalledOnce();
    expect(writeText).toHaveBeenCalledWith(serializeInstanceEvents('backend-1', [response.events[1]]));
    expect(writeText.mock.calls[0][0]).toContain('Event loop did not respond');
    expect(writeText.mock.calls[0][0]).not.toContain('LLM call failed');
    expect(copyButtons[1].title).toBe('已复制');
    expect(copyButtons[0].title).toBe('复制该事件诊断');
  });

  test('ignores metrics truncation while retaining Watchdog truncation warnings', async () => {
    apiMocks.getAdminInstanceEvents.mockResolvedValue({
      instance_id: 'backend-1',
      sources: {
        error_log: { available: true, truncated: false },
        loop_lag: { available: true, truncated: true },
        metrics: { available: true, truncated: true },
      },
      events: [],
    });

    await act(async () => {
      root.render(
        <InstanceEventDrawer
          instance={instance}
          initialFilter="wedge"
          onClose={vi.fn()}
          onOpenConversation={vi.fn()}
        />,
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Watchdog 日志超过单次扫描上限');
    expect(container.textContent).not.toContain('运行指标超过单次扫描上限');
  });

  test('flags only events whose earlier metric snapshot may be outside the scan', async () => {
    apiMocks.getAdminInstanceEvents.mockResolvedValue({
      instance_id: 'backend-1',
      sources: {
        error_log: { available: true, truncated: false },
        loop_lag: { available: true, truncated: false },
        metrics: { available: true, truncated: true },
      },
      events: [
        {
          id: 'wedge-old',
          type: 'wedge',
          source: 'loop_lag',
          severity: 'error',
          ts: '2026-07-01T07:08:22',
          summary: 'Historical wedge',
          instance_id: 'backend-1',
        },
        {
          id: 'lag-recent',
          type: 'loop_lag',
          source: 'loop_lag',
          severity: 'warning',
          ts: '2026-07-30T07:08:22',
          summary: 'Recent lag without a later sample',
          instance_id: 'backend-1',
          metrics_before: {
            ts: '2026-07-30T07:08:00',
            process: { cpu_pct: 1.2 },
          },
        },
      ],
    });

    await act(async () => {
      root.render(
        <InstanceEventDrawer
          instance={instance}
          initialFilter="all"
          onClose={vi.fn()}
          onOpenConversation={vi.fn()}
        />,
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const warning = '未找到事件前运行指标，指标快照可能不完整。';
    expect(container.textContent?.split(warning)).toHaveLength(2);
  });
});

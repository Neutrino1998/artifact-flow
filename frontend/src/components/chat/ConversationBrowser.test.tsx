import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useConversationStore } from '@/stores/conversationStore';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';
import ConversationBrowser from './ConversationBrowser';

const apiMocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  deleteConversation: vi.fn(),
  bulkDeleteConversations: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  listConversations: apiMocks.listConversations,
  deleteConversation: apiMocks.deleteConversation,
  bulkDeleteConversations: apiMocks.bulkDeleteConversations,
}));

vi.mock('@/hooks/useChat', () => ({
  useChat: () => ({ switchConversation: vi.fn() }),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const conversation = (id: string, title: string, activeMessageId: string | null = null) => ({
  id,
  title,
  message_count: 1,
  created_at: '2026-08-05T00:00:00',
  updated_at: '2026-08-05T00:00:00',
  active_message_id: activeMessageId,
  upload_bytes: 0,
});

describe('ConversationBrowser bulk delete', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useConversationStore.getState().reset();
    useUIStore.setState(INITIAL_UI_STATE);
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.replaceChildren();
    useConversationStore.getState().reset();
    useUIStore.setState(INITIAL_UI_STATE);
  });

  it('shows partial failure and keeps newly-active conversations selected', async () => {
    apiMocks.listConversations
      .mockResolvedValueOnce({
        conversations: [conversation('conv-1', 'First'), conversation('conv-2', 'Second')],
        total: 2,
        has_more: false,
      })
      .mockResolvedValueOnce({
        conversations: [conversation('conv-2', 'Second', 'msg-running')],
        total: 1,
        has_more: false,
      });
    apiMocks.bulkDeleteConversations.mockResolvedValue({
      deleted: ['conv-1'],
      failed: [{ id: 'conv-2', reason: 'active_execution' }],
    });

    await act(async () => {
      root.render(<ConversationBrowser />);
    });

    const bulkManage = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '批量管理',
    );
    await act(async () => bulkManage?.click());

    const first = container.querySelector<HTMLInputElement>('input[aria-label="选中 First"]');
    const second = container.querySelector<HTMLInputElement>('input[aria-label="选中 Second"]');
    await act(async () => {
      first?.click();
      second?.click();
    });

    const openConfirm = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('删除 (2)'),
    );
    await act(async () => openConfirm?.click());

    const confirm = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('确认删除'),
    );
    await act(async () => confirm?.click());

    expect(apiMocks.bulkDeleteConversations).toHaveBeenCalledWith(['conv-1', 'conv-2']);
    expect(container.textContent).toContain(
      '已删除 1 条。1 条任务正在运行，已保留选择，完成或取消后可重试。',
    );
    expect(container.textContent).toContain('选择模式');
    expect(
      container.querySelector<HTMLInputElement>('input[aria-label="选中 Second"]')?.checked,
    ).toBe(true);
  });
});

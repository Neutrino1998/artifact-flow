import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AdminConversationList from './AdminConversationList';
import NotificationConfigList from './NotificationConfigList';
import { useNotificationConfigStore } from '@/stores/notificationConfigStore';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';

const apiMocks = vi.hoisted(() => ({
  listAdminConversations: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  listAdminConversations: apiMocks.listAdminConversations,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('sidebar navigation lists', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useUIStore.setState(INITIAL_UI_STATE);
    useNotificationConfigStore.setState({
      items: [],
      selectedIndex: null,
      loading: false,
      dirty: false,
    });
    apiMocks.listAdminConversations.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useUIStore.setState(INITIAL_UI_STATE);
    useNotificationConfigStore.setState({
      items: [],
      selectedIndex: null,
      loading: false,
      dirty: false,
    });
  });

  it('closes after selecting or browsing admin conversations', async () => {
    apiMocks.listAdminConversations.mockResolvedValue({
      conversations: [{
        id: 'conv-1',
        title: 'Audit conversation',
        user_id: 'user-1',
        user_display_name: 'User One',
        message_count: 2,
        is_active: false,
        active_message_id: null,
        created_at: '2026-07-23T00:00:00',
        updated_at: '2026-07-23T00:00:00',
      }],
      total: 21,
      has_more: true,
    });
    const onNavigate = vi.fn();

    await act(async () => {
      root.render(<AdminConversationList onNavigate={onNavigate} />);
    });

    const conversation = container.querySelector<HTMLElement>('.cursor-pointer');
    const showAll = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '显示所有对话',
    );
    expect(conversation).not.toBeNull();
    expect(showAll).toBeDefined();

    await act(async () => conversation?.click());
    expect(useUIStore.getState().observabilitySelectedConvId).toBe('conv-1');
    expect(onNavigate).toHaveBeenCalledTimes(1);

    await act(async () => showAll?.click());
    expect(useUIStore.getState().observabilityBrowseVisible).toBe(true);
    expect(onNavigate).toHaveBeenCalledTimes(2);
  });

  it('closes after selecting a notification', async () => {
    useNotificationConfigStore.setState({
      items: [{
        id: 'notice-1',
        severity: 'info',
        title: 'Maintenance',
        body: 'Scheduled maintenance',
        starts_at: null,
        ends_at: null,
        dismissible: true,
      }],
      selectedIndex: null,
      loading: false,
    });
    const onNavigate = vi.fn();

    await act(async () => {
      root.render(<NotificationConfigList onNavigate={onNavigate} />);
    });

    const notification = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Maintenance'),
    );
    expect(notification).toBeDefined();

    await act(async () => notification?.click());

    expect(useNotificationConfigStore.getState().selectedIndex).toBe(0);
    expect(onNavigate).toHaveBeenCalledOnce();
  });
});

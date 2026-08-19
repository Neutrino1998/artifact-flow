import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import NotificationCenter from './NotificationCenter';
import { useAuthStore } from '@/stores/authStore';

const notificationMocks = vi.hoisted(() => ({
  dismissNotification: vi.fn(),
  fetchNotifications: vi.fn(),
  markNotificationsSeen: vi.fn(),
  unseenNotificationIds: vi.fn(),
}));

vi.mock('@/lib/siteConfig', () => ({
  dismissNotification: notificationMocks.dismissNotification,
  fetchNotifications: notificationMocks.fetchNotifications,
  markNotificationsSeen: notificationMocks.markNotificationsSeen,
  unseenNotificationIds: notificationMocks.unseenNotificationIds,
}));

vi.mock('@/components/markdown/MarkdownBlock', () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

const NOTICE = {
  id: 'notice-1',
  severity: 'info' as const,
  title: '系统通知',
  body: '通知正文',
  dismissible: true,
};

describe('NotificationCenter first-view popup', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    notificationMocks.dismissNotification.mockReset();
    notificationMocks.fetchNotifications.mockReset();
    notificationMocks.markNotificationsSeen.mockReset();
    notificationMocks.unseenNotificationIds.mockReset();
    useAuthStore.setState({
      user: {
        id: 'user-1',
        username: 'user',
        display_name: null,
        role: 'user',
        auth_provider: 'local_password',
        can_change_password: true,
        can_edit_profile: true,
        must_change_password: false,
        department_path: null,
      },
      token: 'token',
      isAuthenticated: true,
      isHydrated: true,
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useAuthStore.setState({
      user: null,
      token: null,
      isAuthenticated: false,
      isHydrated: false,
    });
  });

  it('opens automatically and marks newly seen ids in browser state', async () => {
    notificationMocks.fetchNotifications.mockResolvedValue([NOTICE]);
    notificationMocks.unseenNotificationIds.mockReturnValue(['notice-1']);

    await act(async () => {
      root.render(<NotificationCenter />);
    });

    expect(notificationMocks.fetchNotifications).toHaveBeenCalledWith('user-1');
    expect(notificationMocks.markNotificationsSeen).toHaveBeenCalledWith(
      'user-1',
      ['notice-1'],
    );
    expect(container.querySelector('h2')?.textContent).toBe('通知 (1)');
  });

  it('keeps an already-seen notification collapsed until the user clicks it', async () => {
    notificationMocks.fetchNotifications.mockResolvedValue([NOTICE]);
    notificationMocks.unseenNotificationIds.mockReturnValue([]);

    await act(async () => {
      root.render(<NotificationCenter />);
    });

    expect(notificationMocks.markNotificationsSeen).not.toHaveBeenCalled();
    expect(container.querySelector('h2')).toBeNull();
    expect(container.textContent).toContain('系统通知');
  });
});

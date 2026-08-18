import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import UserMenu from './UserMenu';
import { useAuthStore } from '@/stores/authStore';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';
import { readTaskNotificationPreference } from '@/lib/taskNotifications';

vi.mock('./StorageBar', () => ({ default: () => null }));
vi.mock('@/components/layout/ChangePasswordDialog', () => ({ default: () => null }));
vi.mock('@/components/layout/EditDisplayNameDialog', () => ({ default: () => null }));

class MockNotification {
  static permission: NotificationPermission = 'default';
  static requestPermission = vi.fn(async () => MockNotification.permission);
}

describe('UserMenu task notification switch', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    window.localStorage.clear();
    MockNotification.permission = 'default';
    MockNotification.requestPermission.mockReset();
    MockNotification.requestPermission.mockImplementation(async () => MockNotification.permission);
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: MockNotification,
    });
    useUIStore.setState(INITIAL_UI_STATE);
    useAuthStore.setState({
      user: {
        id: 'user-1',
        username: 'tester',
        display_name: 'Tester',
        role: 'user',
        auth_provider: 'local_password',
        can_change_password: true,
        must_change_password: false,
        department_path: null,
      },
      token: 'test-token',
      isAuthenticated: true,
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useUIStore.setState(INITIAL_UI_STATE);
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });
  });

  it('shows the switch on by default and persists an opt-out without prompting', async () => {
    await act(async () => {
      root.render(<UserMenu />);
    });

    const trigger = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Tester'),
    );
    expect(trigger).toBeDefined();
    await act(async () => trigger?.click());

    const toggle = container.querySelector<HTMLButtonElement>('[role="switch"]');
    expect(toggle?.getAttribute('aria-checked')).toBe('true');
    expect(toggle?.querySelector('span span')?.className).toContain('translate-x-4');
    expect(container.textContent).toContain('首次发送任务时询问权限');

    await act(async () => toggle?.click());

    expect(toggle?.getAttribute('aria-checked')).toBe('false');
    expect(toggle?.querySelector('span span')?.className).toContain('translate-x-0');
    expect(readTaskNotificationPreference('user-1')).toBe(false);
    expect(MockNotification.requestPermission).not.toHaveBeenCalled();
  });

  it('uses the resolved permission immediately even if the static property still lags', async () => {
    await act(async () => {
      root.render(<UserMenu />);
    });
    const trigger = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Tester'),
    );
    await act(async () => trigger?.click());

    const toggle = container.querySelector<HTMLButtonElement>('[role="switch"]');
    await act(async () => toggle?.click()); // opt out first
    MockNotification.requestPermission.mockResolvedValueOnce('granted');

    await act(async () => {
      toggle?.click();
      await Promise.resolve();
    });

    // MockNotification.permission intentionally remains "default". The menu
    // must trust requestPermission's granted result rather than need a reload.
    expect(toggle?.getAttribute('aria-checked')).toBe('true');
    expect(toggle?.querySelector('span span')?.className).toContain('translate-x-4');
    expect(container.textContent).not.toContain('首次发送任务时询问权限');
    expect(readTaskNotificationPreference('user-1')).toBe(true);
  });
});

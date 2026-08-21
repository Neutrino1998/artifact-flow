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
vi.mock('@/components/layout/PersonalAccessTokenDialog', () => ({ default: () => null }));

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
        can_edit_profile: true,
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

  it('does not offer local profile or password actions for a provider-managed identity', async () => {
    useAuthStore.setState({
      user: {
        ...useAuthStore.getState().user!,
        auth_provider: 'company-sso',
        can_change_password: false,
        can_edit_profile: false,
      },
    });
    await act(async () => {
      root.render(<UserMenu />);
    });

    const trigger = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Tester'),
    );
    await act(async () => trigger?.click());

    expect(container.textContent).not.toContain('显示名和部门由企业认证维护');
    expect(container.textContent).not.toContain('修改显示名');
    expect(container.textContent).not.toContain('修改密码');
  });

  it('draws the dark-theme sun rays at one consistent radius', async () => {
    await act(async () => {
      root.render(<UserMenu />);
    });

    const trigger = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Tester'),
    );
    await act(async () => trigger?.click());

    const themeButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('浅色模式'),
    );
    expect(themeButton?.querySelector('circle[cx="8"][cy="8"][r="3"]')).not.toBeNull();
    expect(themeButton?.querySelector('path[d="M8 1.25v1.5M8 13.25v1.5M1.25 8h1.5M13.25 8h1.5"]')).not.toBeNull();
    expect(themeButton?.querySelector('path[d="m3.25 3.25 1.05 1.05m8.45-1.05L11.7 4.3m1.05 8.45-1.05-1.05m-8.45 1.05L4.3 11.7"]')).not.toBeNull();
  });

  it('visually distinguishes admin actions and the two notification controls', async () => {
    useAuthStore.setState({
      user: {
        ...useAuthStore.getState().user!,
        role: 'admin',
      },
    });
    await act(async () => {
      root.render(<UserMenu />);
    });

    const trigger = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Tester'),
    );
    await act(async () => trigger?.click());

    for (const label of ['用户管理', '工具管理', '部门授权', '会话监控', '实例监控', '通知管理']) {
      const button = Array.from(container.querySelectorAll('button')).find(
        (candidate) => candidate.textContent?.includes(label),
      );
      const pill = Array.from(button?.querySelectorAll('span') ?? []).find(
        (candidate) => candidate.textContent === 'admin',
      );
      expect(pill, `${label} should carry an admin pill`).toBeDefined();
    }

    expect(container.querySelector('path[d^="M8 1.5V3"]')).not.toBeNull();
    expect(container.querySelector('path[d^="M5 5.5 12.5 2"]')).not.toBeNull();

    const apiKeyButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('API 密钥'),
    );
    expect(apiKeyButton?.querySelector('svg')?.getAttribute('width')).toBe('13');
  });
});

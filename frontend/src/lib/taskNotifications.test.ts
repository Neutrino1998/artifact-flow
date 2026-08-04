import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { APP_NAME } from '@/lib/branding';
import {
  enableTaskNotifications,
  notifyTaskTerminal,
  readTaskNotificationPreference,
  requestDefaultTaskNotificationPermission,
  setTaskNotificationPreference,
  taskNotificationsEnabled,
} from './taskNotifications';

interface NotificationInstance {
  title: string;
  options?: NotificationOptions;
  onclick: (() => void) | null;
  close: ReturnType<typeof vi.fn>;
}

function installNotificationMock(permission: NotificationPermission) {
  const instances: NotificationInstance[] = [];

  class MockNotification implements NotificationInstance {
    static permission: NotificationPermission = permission;
    static requestPermission = vi.fn(async () => MockNotification.permission);

    onclick: (() => void) | null = null;
    close = vi.fn();

    constructor(public title: string, public options?: NotificationOptions) {
      instances.push(this);
    }
  }

  Object.defineProperty(window, 'Notification', {
    configurable: true,
    value: MockNotification,
  });
  return { MockNotification, instances };
}

describe('task notifications', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('defaults the per-user preference to enabled and persists opt-out', () => {
    installNotificationMock('default');

    expect(readTaskNotificationPreference('user-1')).toBe(true);
    expect(taskNotificationsEnabled('user-1')).toBe(true);

    setTaskNotificationPreference('user-1', false);
    expect(readTaskNotificationPreference('user-1')).toBe(false);
    expect(taskNotificationsEnabled('user-1')).toBe(false);
    expect(readTaskNotificationPreference('user-2')).toBe(true);
  });

  it('requests permission on the first default-enabled task and disables after rejection', async () => {
    const { MockNotification } = installNotificationMock('default');
    MockNotification.requestPermission.mockResolvedValueOnce('denied');

    requestDefaultTaskNotificationPermission('user-1');
    await vi.waitFor(() => expect(MockNotification.requestPermission).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(readTaskNotificationPreference('user-1')).toBe(false));
  });

  it('enables an opted-out preference after explicit browser permission', async () => {
    const { MockNotification } = installNotificationMock('default');
    MockNotification.requestPermission.mockResolvedValueOnce('granted');
    setTaskNotificationPreference('user-1', false);

    await expect(enableTaskNotifications('user-1')).resolves.toBe(true);
    expect(readTaskNotificationPreference('user-1')).toBe(true);
  });

  it('uses configured branding and a stable message tag for terminal notifications', () => {
    const { instances } = installNotificationMock('granted');
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => {});

    notifyTaskTerminal('user-1', 'message-7', 'complete');

    expect(instances).toHaveLength(1);
    expect(instances[0].title).toBe(APP_NAME);
    expect(instances[0].options).toMatchObject({
      body: '任务已完成',
      tag: 'task-message-7',
    });

    instances[0].onclick?.();
    expect(focus).toHaveBeenCalledOnce();
    expect(instances[0].close).toHaveBeenCalledOnce();
  });

  it('does not notify when the user opted out or permission is not granted', () => {
    const denied = installNotificationMock('denied');
    notifyTaskTerminal('user-1', 'message-1', 'error');
    expect(denied.instances).toHaveLength(0);

    const granted = installNotificationMock('granted');
    setTaskNotificationPreference('user-1', false);
    notifyTaskTerminal('user-1', 'message-2', 'timed_out');
    expect(granted.instances).toHaveLength(0);
  });
});

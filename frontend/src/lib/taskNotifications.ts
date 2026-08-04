import { APP_NAME } from '@/lib/branding';

const PREFERENCE_KEY_PREFIX = 'af.task_notifications.enabled.';

/** Same-tab companion to the native `storage` event (which only fires elsewhere). */
export const TASK_NOTIFICATION_PREFERENCE_EVENT = 'af:task-notifications-preference';

export interface TaskNotificationPreferenceDetail {
  userId: string;
  enabled: boolean;
  permission?: NotificationPermission;
}

export type TaskTerminalStatus = 'complete' | 'timed_out' | 'error';

const TERMINAL_BODY: Record<TaskTerminalStatus, string> = {
  complete: '任务已完成',
  timed_out: '任务执行超时',
  error: '任务执行失败',
};

function preferenceKey(userId: string): string {
  return `${PREFERENCE_KEY_PREFIX}${encodeURIComponent(userId)}`;
}

export function taskNotificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

/**
 * Product preference only. Absence means enabled: task notifications are opt-out,
 * while the browser's origin-level permission remains an independent hard gate.
 */
export function readTaskNotificationPreference(userId: string): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(preferenceKey(userId)) !== 'false';
  } catch {
    // Storage is best-effort. Failing open preserves the product default; the
    // browser permission still prevents notifications without user consent.
    return true;
  }
}

export function setTaskNotificationPreference(
  userId: string,
  enabled: boolean,
  permission?: NotificationPermission,
): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(preferenceKey(userId), String(enabled));
  } catch {
    // A blocked/full localStorage only loses preference persistence. Notification
    // delivery still remains gated by the browser's origin-level permission.
  }
  // Carry the permission result itself. Some browsers resolve
  // requestPermission() before their static Notification.permission property
  // reflects the new value; listeners should not need a page refresh to learn
  // the authoritative result they just received.
  window.dispatchEvent(new CustomEvent<TaskNotificationPreferenceDetail>(
    TASK_NOTIFICATION_PREFERENCE_EVENT,
    { detail: { userId, enabled, permission } },
  ));
}

/** Effective switch state for display; denied/unsupported cannot be "on". */
export function taskNotificationsEnabled(userId: string): boolean {
  if (!taskNotificationsSupported()) return false;
  return readTaskNotificationPreference(userId) && window.Notification.permission !== 'denied';
}

/**
 * Explicit switch-on path. Must be called from a user gesture so browsers are
 * allowed to show their permission prompt.
 */
export async function enableTaskNotifications(userId: string): Promise<boolean> {
  if (!taskNotificationsSupported()) {
    setTaskNotificationPreference(userId, false);
    return false;
  }

  if (window.Notification.permission === 'denied') {
    setTaskNotificationPreference(userId, false);
    return false;
  }

  const permission = window.Notification.permission === 'granted'
    ? 'granted'
    : await window.Notification.requestPermission();
  const enabled = permission === 'granted';
  setTaskNotificationPreference(userId, enabled, permission);
  return enabled;
}

/**
 * Task notifications are enabled by default (opt-out), so the first real task
 * send is the one-time permission request gesture. Do not await this from the
 * send path: the task should start while the browser owns the permission UI.
 */
export function requestDefaultTaskNotificationPermission(userId: string): void {
  if (!readTaskNotificationPreference(userId) || !taskNotificationsSupported()) return;

  if (window.Notification.permission === 'denied') {
    setTaskNotificationPreference(userId, false);
    return;
  }
  if (window.Notification.permission === 'default') {
    void enableTaskNotifications(userId);
  }
}

/** Best-effort system notification; the task result itself remains authoritative. */
export function notifyTaskTerminal(
  userId: string,
  messageId: string,
  status: TaskTerminalStatus,
): void {
  if (!readTaskNotificationPreference(userId) || !taskNotificationsSupported()) return;
  if (window.Notification.permission !== 'granted') return;

  try {
    const notification = new window.Notification(APP_NAME, {
      body: TERMINAL_BODY[status],
      // Reconnect/replay may redeliver a terminal. The stable tag makes the OS
      // replace the prior notification instead of stacking a duplicate.
      tag: `task-${messageId}`,
    });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch (error) {
    // Notification delivery is optional UX. A platform-level constructor error
    // must never disturb terminal SSE handling or result persistence.
    console.warn('Failed to show task notification:', error);
  }
}

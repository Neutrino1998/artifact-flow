import { beforeEach, describe, expect, test } from 'vitest';
import { useNotificationConfigStore } from './notificationConfigStore';

const existingNotification = {
  id: 'existing-notice',
  severity: 'info' as const,
  title: 'Existing notice',
  body: 'Existing body',
  starts_at: null,
  ends_at: null,
  dismissible: true,
};

describe('notificationConfigStore', () => {
  beforeEach(() => {
    useNotificationConfigStore.setState({
      items: [existingNotification],
      selectedIndex: 0,
      dirty: false,
      message: '已保存',
      error: null,
      previewMode: 'preview',
    });
  });

  test('prepends and selects a new notification', () => {
    useNotificationConfigStore.getState().addNotification();

    const state = useNotificationConfigStore.getState();
    expect(state.items).toHaveLength(2);
    expect(state.items[0].id).toMatch(/^notice-\d{4}-\d{2}-\d{2}$/);
    expect(state.items[1]).toEqual(existingNotification);
    expect(state.selectedIndex).toBe(0);
    expect(state.dirty).toBe(true);
    expect(state.message).toBeNull();
    expect(state.previewMode).toBe('edit');
  });
});

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Sidebar from './Sidebar';
import { useAuthStore } from '@/stores/authStore';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';

const chatMocks = vi.hoisted(() => ({
  startNewChat: vi.fn(),
}));

vi.mock('@/features/chat/runtime/useChat', () => ({
  useChat: () => ({ startNewChat: chatMocks.startNewChat }),
}));

vi.mock('./ConversationList', () => ({ default: () => <div>Conversation list</div> }));
vi.mock('@/features/admin/observability/AdminConversationList', () => ({ default: () => null }));
vi.mock('@/features/admin/notifications/NotificationConfigList', () => ({ default: () => null }));
vi.mock('./NotificationCenter', () => ({ default: () => null }));
vi.mock('./UserMenu', () => ({ default: () => null }));
vi.mock('@/components/BrandingFooter', () => ({ default: () => null }));

describe('Sidebar drawer presentation', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useUIStore.setState({ ...INITIAL_UI_STATE, sidebarCollapsed: true });
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });
    chatMocks.startNewChat.mockReset();
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

  it('ignores the desktop collapsed rail state and closes after navigation', async () => {
    const onNavigate = vi.fn();
    await act(async () => {
      root.render(<Sidebar variant="drawer" onNavigate={onNavigate} />);
    });

    expect(container.querySelector('h1')?.textContent).toBe('ArtifactFlow');
    expect(container.querySelector('button[aria-label="展开侧栏"]')).toBeNull();
    expect(container.querySelector('button[aria-label="收起侧栏"]')).not.toBeNull();

    const newChat = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('新建对话'),
    );
    expect(newChat).toBeDefined();

    await act(async () => newChat?.click());

    expect(chatMocks.startNewChat).toHaveBeenCalledOnce();
    expect(onNavigate).toHaveBeenCalledOnce();
  });

  it('closes the drawer when creating a notification', async () => {
    useAuthStore.setState({
      user: {
        id: 'admin-1',
        username: 'admin',
        display_name: 'Admin',
        role: 'admin',
        auth_provider: 'local_password',
        can_change_password: true,
        must_change_password: false,
        department_path: null,
      },
      token: 'test-token',
      isAuthenticated: true,
    });
    useUIStore.setState({ activeMode: 'notificationConfig' });
    const onNavigate = vi.fn();

    await act(async () => {
      root.render(<Sidebar variant="drawer" onNavigate={onNavigate} />);
    });

    const createNotification = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '新建通知',
    );
    expect(createNotification).toBeDefined();

    await act(async () => createNotification?.click());

    expect(useUIStore.getState().notificationConfigCreateRequestId).toBe(1);
    expect(onNavigate).toHaveBeenCalledOnce();
  });
});

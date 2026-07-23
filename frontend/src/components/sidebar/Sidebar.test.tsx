import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Sidebar from './Sidebar';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';

const chatMocks = vi.hoisted(() => ({
  startNewChat: vi.fn(),
}));

vi.mock('@/hooks/useChat', () => ({
  useChat: () => ({ startNewChat: chatMocks.startNewChat }),
}));

vi.mock('./ConversationList', () => ({ default: () => <div>Conversation list</div> }));
vi.mock('./AdminConversationList', () => ({ default: () => null }));
vi.mock('./NotificationConfigList', () => ({ default: () => null }));
vi.mock('./NotificationCenter', () => ({ default: () => null }));
vi.mock('./UserMenu', () => ({ default: () => null }));
vi.mock('@/components/BrandingFooter', () => ({ default: () => null }));

describe('Sidebar drawer presentation', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useUIStore.setState({ ...INITIAL_UI_STATE, sidebarCollapsed: true });
    chatMocks.startNewChat.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useUIStore.setState(INITIAL_UI_STATE);
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
});

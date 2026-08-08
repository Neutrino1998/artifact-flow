import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';
import ObservabilityPanel from './ObservabilityPanel';

const apiMocks = vi.hoisted(() => ({
  getAdminConversationEvents: vi.fn(),
  listAdminConversationArtifacts: vi.fn(),
  listAdminFeedback: vi.fn(),
  listAdminConversations: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  getAdminConversationEvents: apiMocks.getAdminConversationEvents,
  listAdminConversationArtifacts: apiMocks.listAdminConversationArtifacts,
  listAdminFeedback: apiMocks.listAdminFeedback,
  listAdminConversations: apiMocks.listAdminConversations,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const eventsResponse = {
  conversation_id: 'conv-1',
  title: '反馈定位测试',
  user_id: 'user-1',
  user_display_name: 'User',
  active_branch: 'msg-issue',
  is_active: false,
  active_message_id: null,
  created_at: '2026-08-07T00:00:00',
  updated_at: '2026-08-07T00:01:00',
  messages: [
    {
      message_id: 'msg-target',
      parent_id: null,
      user_input: '普通反馈消息',
      response: 'response',
      created_at: '2026-08-07T00:00:00',
      events: [],
      execution_metrics: null,
      feedback: null,
      uploaded_files: null,
    },
    {
      message_id: 'msg-issue',
      parent_id: 'msg-target',
      user_input: '异常消息',
      response: null,
      created_at: '2026-08-07T00:01:00',
      events: [{
        id: 1,
        event_id: 'event-1',
        event_type: 'error',
        agent_name: 'lead_agent',
        data: { error: 'boom' },
        created_at: '2026-08-07T00:01:01',
      }],
      execution_metrics: null,
      feedback: null,
      uploaded_files: null,
    },
  ],
};

function buttonWithText(container: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === text,
  );
}

describe('ObservabilityPanel feedback focus', () => {
  let container: HTMLDivElement;
  let root: Root;
  let scrollIntoView: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    useUIStore.setState({
      ...INITIAL_UI_STATE,
      activeMode: 'observability',
      observabilitySelectedConvId: 'conv-1',
    });
    apiMocks.getAdminConversationEvents.mockReset();
    apiMocks.getAdminConversationEvents.mockResolvedValue(eventsResponse);
    apiMocks.listAdminConversationArtifacts.mockReset();
    apiMocks.listAdminConversationArtifacts.mockResolvedValue({ artifacts: [] });
    apiMocks.listAdminFeedback.mockReset();
    apiMocks.listAdminFeedback.mockResolvedValue({ feedback: [], total: 0, has_more: false });
    apiMocks.listAdminConversations.mockReset();
    apiMocks.listAdminConversations.mockResolvedValue({ conversations: [], total: 0, has_more: false });
    scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.replaceChildren();
    useUIStore.setState(INITIAL_UI_STATE);
  });

  async function renderPanel() {
    await act(async () => {
      root.render(<ObservabilityPanel />);
    });
  }

  async function focusTargetMessage() {
    await act(async () => {
      useUIStore.getState().openObservabilityMessage('conv-1', 'msg-target');
    });
  }

  async function openAndCloseBrowser(browser: 'feedback' | 'conversations') {
    await act(async () => {
      useUIStore.getState().setObservabilityBrowser(browser);
    });
    await act(async () => {
      useUIStore.getState().setObservabilityBrowser('none');
    });
  }

  it('shows both the local date and time for each event', async () => {
    await renderPanel();

    const eventButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('error'),
    );
    expect(eventButton?.textContent).toMatch(/\d{4}.*\d{1,2}:\d{2}:\d{2}/);
  });

  it('returns from Artifacts before locating feedback in the current conversation', async () => {
    await renderPanel();
    await act(async () => buttonWithText(container, 'Artifacts')?.click());
    expect(buttonWithText(container, 'Artifacts')?.getAttribute('aria-selected')).toBe('true');

    await focusTargetMessage();

    expect(buttonWithText(container, 'Events')?.getAttribute('aria-selected')).toBe('true');
    expect(document.getElementById('admin-message-msg-target')).not.toBeNull();
    expect(scrollIntoView).toHaveBeenCalledOnce();
  });

  it('turns off issues-only before locating a normal feedback message', async () => {
    await renderPanel();
    await act(async () => buttonWithText(container, '只看异常')?.click());
    expect(document.getElementById('admin-message-msg-target')).toBeNull();

    await focusTargetMessage();

    expect(buttonWithText(container, '只看异常')).toBeDefined();
    expect(document.getElementById('admin-message-msg-target')).not.toBeNull();
    expect(scrollIntoView).toHaveBeenCalledOnce();
  });

  it.each(['feedback', 'conversations'] as const)(
    'does not replay consumed focus after opening and closing the %s browser',
    async (browser) => {
      await renderPanel();
      await focusTargetMessage();
      expect(useUIStore.getState().observabilityFocusConsumedId).toBe(1);
      scrollIntoView.mockClear();

      await act(async () => buttonWithText(container, 'Artifacts')?.click());
      await openAndCloseBrowser(browser);

      expect(buttonWithText(container, 'Artifacts')?.getAttribute('aria-selected')).toBe('true');
      expect(scrollIntoView).not.toHaveBeenCalled();
    },
  );

  it('keeps issues-only when a browser closes without a new focus request', async () => {
    await renderPanel();
    await focusTargetMessage();
    scrollIntoView.mockClear();
    await act(async () => buttonWithText(container, '只看异常')?.click());
    expect(document.getElementById('admin-message-msg-target')).toBeNull();

    await openAndCloseBrowser('feedback');

    expect(buttonWithText(container, '显示全部')).toBeDefined();
    expect(document.getElementById('admin-message-msg-target')).toBeNull();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it('locates the same highlighted message again when explicitly reselected', async () => {
    await renderPanel();
    await focusTargetMessage();
    expect(scrollIntoView).toHaveBeenCalledOnce();

    await focusTargetMessage();

    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    const state = useUIStore.getState();
    expect(state.observabilityFocusRequestId).toBe(2);
    expect(state.observabilityFocusConsumedId).toBe(2);
  });
});

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EventDetailPanel from './EventDetailPanel';

const apiMocks = vi.hoisted(() => ({
  getAdminPromptReconstruct: vi.fn(),
  getAdminLlmCallReconstruct: vi.fn(),
}));

vi.mock('@/lib/api', () => apiMocks);

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe('EventDetailPanel LLM call reconstruction', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    apiMocks.getAdminPromptReconstruct.mockReset();
    apiMocks.getAdminLlmCallReconstruct.mockReset();
    apiMocks.getAdminLlmCallReconstruct.mockResolvedValue({
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      agent_start_event_id: 'evt-start',
      llm_complete_event_id: 'evt-llm',
      agent_name: 'lead_agent',
      model: 'test-model',
      exposed_tool_names: ['search'],
      has_reminder: true,
      messages: [
        { role: 'system', content: 'system prompt' },
        { role: 'user', content: 'question' },
      ],
      response: {
        content: 'persisted-response-marker',
        reasoning_content: 'persisted reasoning',
        tool_calls: [],
      },
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('reconstructs request messages and response from llm_complete', async () => {
    await act(async () => {
      root.render(
        <EventDetailPanel
          event={{
            id: 7,
            event_id: 'evt-llm',
            event_type: 'llm_complete',
            agent_name: 'lead_agent',
            data: {
              model: 'test-model',
              duration_ms: 42,
              content: 'visible final answer',
              tool_calls: [],
            },
            created_at: '2026-08-13T08:00:00',
          }}
          conversationId="conv-1"
          messageId="msg-1"
          onClose={vi.fn()}
        />,
      );
    });

    const reconstructButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === '重建完整调用',
    );
    expect(reconstructButton).toBeDefined();

    await act(async () => {
      reconstructButton?.click();
    });

    expect(apiMocks.getAdminLlmCallReconstruct).toHaveBeenCalledWith(
      'conv-1',
      'msg-1',
      'evt-llm',
    );
    expect(container.textContent).toContain('重建 Messages');
    expect(container.textContent).toContain('模型 Response');
    expect(container.textContent).toContain('persisted-response-marker');
  });

  it('keeps agent_start reconstruction on the prompt-only endpoint', async () => {
    apiMocks.getAdminPromptReconstruct.mockResolvedValue({
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      agent_start_event_id: 'evt-start',
      agent_name: 'lead_agent',
      model: 'test-model',
      exposed_tool_names: [],
      has_reminder: true,
      messages: [
        { role: 'system', content: 'system prompt' },
        { role: 'user', content: 'question' },
      ],
    });

    await act(async () => {
      root.render(
        <EventDetailPanel
          event={{
            id: 6,
            event_id: 'evt-start',
            event_type: 'agent_start',
            agent_name: 'lead_agent',
            data: {
              model: 'test-model',
              system_prompt: 'system prompt',
              reminder: 'current reminder',
            },
            created_at: '2026-08-13T08:00:00',
          }}
          conversationId="conv-1"
          messageId="msg-1"
          onClose={vi.fn()}
        />,
      );
    });

    const reconstructButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === '重建 Messages',
    );

    await act(async () => {
      reconstructButton?.click();
    });

    expect(apiMocks.getAdminPromptReconstruct).toHaveBeenCalledWith(
      'conv-1',
      'msg-1',
      'evt-start',
    );
    expect(apiMocks.getAdminLlmCallReconstruct).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain('模型 Response');
  });
});

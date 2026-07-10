import { describe, test, expect, beforeEach } from 'vitest';
import { useConversationStore } from './conversationStore';
import type { ConversationSummary } from '@/types';

function summary(id: string, active_message_id: string | null = null): ConversationSummary {
  return {
    id,
    title: `t-${id}`,
    message_count: 0,
    created_at: '2026-05-19T00:00:00',
    updated_at: '2026-05-19T00:00:00',
    active_message_id,
    upload_bytes: 0,
  };
}

describe('conversationStore — active_message_id CAS', () => {
  beforeEach(() => {
    useConversationStore.getState().reset();
  });

  test('clear is a no-op when a newer turn already replaced the id', () => {
    const store = useConversationStore.getState();
    store.setConversations([summary('A', 'msg-old')], 1, false);

    // New turn replaces the active message_id optimistically.
    store.setConversationActiveMessage('A', 'msg-new');

    // Old turn's terminal tries to clear msg-old — cache now holds msg-new,
    // so CAS skips the clear. New turn's dot survives.
    store.clearConversationActiveIfMatch('A', 'msg-old');

    expect(useConversationStore.getState().conversations[0].active_message_id).toBe('msg-new');
  });

  test('clear succeeds when the cached id still matches the terminal id', () => {
    const store = useConversationStore.getState();
    store.setConversations([summary('A', 'msg-1')], 1, false);

    store.clearConversationActiveIfMatch('A', 'msg-1');

    expect(useConversationStore.getState().conversations[0].active_message_id).toBeNull();
  });

  test('terminal snapshot creates a visible new conversation before REST refresh', () => {
    const store = useConversationStore.getState();

    store.applyTerminalMessageSnapshot({
      conversationId: 'conv-new',
      messageId: 'msg-timeout',
      parentId: null,
      userInput: '',
      response: '*Task timed out*',
      executionMetrics: { total_duration_ms: 1_800_000 },
      uploadedFiles: [{ filename: 'brief.docx' }],
    });

    const state = useConversationStore.getState();
    expect(state.current?.id).toBe('conv-new');
    expect(state.current?.title).toBe('Untitled');
    expect(state.current?.active_branch).toBe('msg-timeout');
    expect(state.branchPath.map((m) => m.id)).toEqual(['msg-timeout']);
    expect(state.branchPath[0].response).toBe('*Task timed out*');
    expect(state.branchPath[0].uploaded_files?.[0].filename).toBe('brief.docx');
  });

  test('terminal snapshot updates an existing message response', () => {
    const store = useConversationStore.getState();
    store.setCurrent({
      id: 'conv-a',
      title: 'Existing',
      active_branch: 'msg-1',
      session_id: 'conv-a',
      created_at: '2026-05-19T00:00:00',
      updated_at: '2026-05-19T00:00:00',
      messages: [{
        id: 'msg-1',
        parent_id: null,
        user_input: 'hello',
        response: null,
        created_at: '2026-05-19T00:00:00',
        children: [],
        execution_metrics: null,
        uploaded_files: null,
        active_skills: null,
      }],
    });

    store.applyTerminalMessageSnapshot({
      conversationId: 'conv-a',
      messageId: 'msg-1',
      parentId: null,
      userInput: 'ignored',
      response: 'done',
      executionMetrics: { total_duration_ms: 123 },
    });

    const state = useConversationStore.getState();
    expect(state.current?.title).toBe('Existing');
    expect(state.branchPath[0].response).toBe('done');
    expect(state.branchPath[0].execution_metrics).toEqual({ total_duration_ms: 123 });
  });

  test('terminal snapshot appends a child message in an existing conversation', () => {
    const store = useConversationStore.getState();
    store.setCurrent({
      id: 'conv-a',
      title: 'Existing',
      active_branch: 'msg-parent',
      session_id: 'conv-a',
      created_at: '2026-05-19T00:00:00',
      updated_at: '2026-05-19T00:00:00',
      messages: [{
        id: 'msg-parent',
        parent_id: null,
        user_input: 'parent',
        response: 'parent response',
        created_at: '2026-05-19T00:00:00',
        children: [],
        execution_metrics: null,
        uploaded_files: null,
        active_skills: null,
      }],
    });

    store.applyTerminalMessageSnapshot({
      conversationId: 'conv-a',
      messageId: 'msg-child',
      parentId: 'msg-parent',
      userInput: 'child',
      response: '*Task timed out*',
    });

    const state = useConversationStore.getState();
    expect(state.branchPath.map((m) => m.id)).toEqual(['msg-parent', 'msg-child']);
    expect(state.branchPath[1].response).toBe('*Task timed out*');
  });
});

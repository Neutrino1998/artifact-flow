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
});

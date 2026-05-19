import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest';
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
  };
}

describe('conversationStore — active_message_id merge', () => {
  beforeEach(() => {
    // Fake timers so we can control the wall-clock the store records via
    // Date.now() inside setConversationActiveMessage / clearConversationActiveIfMatch,
    // and the snapshotTakenAt values we pass in for setConversations.
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-19T00:00:00Z'));
    useConversationStore.getState().reset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test('stale snapshot does NOT overwrite a fresher local optimistic write', () => {
    const store = useConversationStore.getState();
    // Seed initial list at t=0 (no active).
    store.setConversations([summary('A')], 1, false, Date.now());

    // Snapshot captured AT THIS MOMENT, then user mutates locally afterwards.
    const staleSnapshotTime = Date.now();
    vi.advanceTimersByTime(100);
    store.setConversationActiveMessage('A', 'msg-new');

    // Now the (already-old) snapshot arrives — its server view is still
    // null because it was captured before the local write. Merge guard
    // must preserve local msg-new.
    store.setConversations([summary('A', null)], 1, false, staleSnapshotTime);

    const after = useConversationStore.getState().conversations[0];
    expect(after.active_message_id).toBe('msg-new');
  });

  test('fresh snapshot overwrites a stale local value', () => {
    const store = useConversationStore.getState();
    store.setConversations([summary('A')], 1, false, Date.now());

    // Local thought it was active…
    store.setConversationActiveMessage('A', 'msg-old');

    // …but later, a NEWER snapshot says server has moved on. Server view
    // is authoritative — take it.
    vi.advanceTimersByTime(500);
    store.setConversations([summary('A', null)], 1, false, Date.now());

    const after = useConversationStore.getState().conversations[0];
    expect(after.active_message_id).toBeNull();
  });

  test('terminal CAS is no-op when a newer turn already replaced the id', () => {
    const store = useConversationStore.getState();
    store.setConversations([summary('A', 'msg-old')], 1, false, Date.now());

    // New turn replaces the active message_id.
    store.setConversationActiveMessage('A', 'msg-new');

    // Old turn's terminal tries to clear msg-old — cached now holds msg-new,
    // so CAS skips the clear.
    store.clearConversationActiveIfMatch('A', 'msg-old');

    const after = useConversationStore.getState().conversations[0];
    expect(after.active_message_id).toBe('msg-new');
  });

  test('terminal CAS clears when the cached id still matches', () => {
    const store = useConversationStore.getState();
    store.setConversations([summary('A', 'msg-1')], 1, false, Date.now());

    store.clearConversationActiveIfMatch('A', 'msg-1');

    const after = useConversationStore.getState().conversations[0];
    expect(after.active_message_id).toBeNull();
  });

  test('merge only protects active_message_id — other fields still take server view', () => {
    const store = useConversationStore.getState();
    store.setConversations([summary('A')], 1, false, Date.now());

    const staleSnapshotTime = Date.now();
    vi.advanceTimersByTime(100);
    store.setConversationActiveMessage('A', 'msg-new');

    // Stale snapshot, but with a NEW title (e.g. renamed in another tab).
    const incoming = { ...summary('A', null), title: 'renamed' };
    store.setConversations([incoming], 1, false, staleSnapshotTime);

    const after = useConversationStore.getState().conversations[0];
    // active_message_id preserved (the whole point of the merge)…
    expect(after.active_message_id).toBe('msg-new');
    // …but the title still takes the server value: the merge is targeted.
    expect(after.title).toBe('renamed');
  });

  test('removeConversation cleans the per-conv mutation timestamp', () => {
    const store = useConversationStore.getState();
    store.setConversations([summary('A'), summary('B')], 2, false, Date.now());
    store.setConversationActiveMessage('A', 'msg-A');
    store.removeConversation('A');

    expect(useConversationStore.getState().localMutationTimes.has('A')).toBe(false);
  });
});

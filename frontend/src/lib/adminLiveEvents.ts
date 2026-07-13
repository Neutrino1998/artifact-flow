import type { AdminConversationEventsResponse, AdminEventItem, AdminMessageGroup } from './api';
import type { SSEEvent } from '@/types/events';

// These events have dedicated live consumers or are too high-frequency for the
// persisted observability timeline.  Semantic execution events remain visible.
const ADMIN_TIMELINE_IGNORED = new Set([
  'metadata',
  'llm_chunk',
  'artifact_created',
  'artifact_updated',
]);

export const ADMIN_TERMINAL_EVENTS = new Set([
  'complete',
  'cancelled',
  'timed_out',
  'error',
]);

export function appendAdminLiveEvent(
  snapshot: AdminConversationEventsResponse,
  messageId: string,
  event: SSEEvent,
  temporaryId: number,
): AdminConversationEventsResponse {
  if (ADMIN_TIMELINE_IGNORED.has(String(event.type))) return snapshot;

  const createdAt = event.timestamp || new Date().toISOString();
  const item: AdminEventItem = {
    id: temporaryId,
    event_id: null,
    event_type: String(event.type),
    agent_name: event.agent ?? null,
    data: event.data ?? null,
    created_at: createdAt,
  };

  const groupIndex = snapshot.messages.findIndex((group) => group.message_id === messageId);
  const liveInput = event.type === 'user_input' && typeof event.data?.content === 'string'
    ? event.data.content
    : null;

  if (groupIndex < 0) {
    const group: AdminMessageGroup = {
      message_id: messageId,
      parent_id: null,
      user_input: liveInput ?? '执行中…',
      response: null,
      created_at: createdAt,
      events: [item],
      execution_metrics: null,
    };
    return { ...snapshot, messages: [...snapshot.messages, group] };
  }

  const messages = snapshot.messages.slice();
  const current = messages[groupIndex];
  messages[groupIndex] = {
    ...current,
    user_input: liveInput ?? current.user_input,
    events: [...current.events, item],
  };
  return { ...snapshot, messages };
}

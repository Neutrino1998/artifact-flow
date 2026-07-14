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

export function formatAdminInputPreview(userInput: string, maxLength = 80): string {
  const trimmed = userInput.trim();
  if (!trimmed) return '无文本输入';
  return trimmed.slice(0, maxLength) + (trimmed.length > maxLength ? '...' : '');
}

export function isAdminMessageOffActiveBranch(
  messageId: string,
  activeMessageId: string | null,
  hasBranches: boolean,
  activePathIds: Set<string>,
): boolean {
  // The lease/stream exists while a queued message is not yet in the DB, so
  // its temporary live group has no authoritative parent. Never call the
  // currently executing message a side branch; terminal refresh replaces it
  // with the durable message and its real parent relationship.
  return messageId !== activeMessageId
    && hasBranches
    && !activePathIds.has(messageId);
}

export function appendAdminLiveEvent(
  snapshot: AdminConversationEventsResponse,
  messageId: string,
  event: SSEEvent,
  temporaryId: number,
): AdminConversationEventsResponse {
  const createdAt = event.timestamp || new Date().toISOString();
  const groupIndex = snapshot.messages.findIndex((group) => group.message_id === messageId);
  const liveInput = event.type === 'metadata' && typeof event.data?.user_input === 'string'
    ? event.data.user_input
    : event.type === 'user_input' && typeof event.data?.content === 'string'
      ? event.data.content
      : null;
  const rawFiles = event.type === 'metadata' && Array.isArray(event.data?.uploaded_files)
    ? event.data.uploaded_files
    : null;
  const liveFiles = rawFiles == null
    ? null
    : rawFiles.flatMap((file) => {
      if (typeof file !== 'object' || file == null || !('filename' in file)) return [];
      const filename = file.filename;
      return typeof filename === 'string' ? [{ filename }] : [];
    });
  const ignored = ADMIN_TIMELINE_IGNORED.has(String(event.type));

  // Ignored events stay out of the event list, but the one-shot metadata event
  // may still improve the best-effort live message preview.
  if (ignored && liveInput == null && liveFiles == null) return snapshot;

  const item: AdminEventItem | null = ignored ? null : {
    id: temporaryId,
    event_id: null,
    event_type: String(event.type),
    agent_name: event.agent ?? null,
    data: event.data ?? null,
    created_at: createdAt,
  };

  if (groupIndex < 0) {
    const group: AdminMessageGroup = {
      message_id: messageId,
      parent_id: null,
      user_input: liveInput ?? '执行中…',
      response: null,
      created_at: createdAt,
      events: item == null ? [] : [item],
      execution_metrics: null,
      uploaded_files: liveFiles,
    };
    return { ...snapshot, messages: [...snapshot.messages, group] };
  }

  const messages = snapshot.messages.slice();
  const current = messages[groupIndex];
  messages[groupIndex] = {
    ...current,
    user_input: liveInput ?? current.user_input,
    uploaded_files: liveFiles ?? current.uploaded_files,
    events: item == null ? current.events : [...current.events, item],
  };
  return { ...snapshot, messages };
}

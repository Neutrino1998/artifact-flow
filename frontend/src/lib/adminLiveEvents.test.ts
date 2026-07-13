import { describe, expect, test } from 'vitest';
import { appendAdminLiveEvent, isAdminMessageOffActiveBranch } from './adminLiveEvents';
import type { AdminConversationEventsResponse } from './api';

function snapshot(): AdminConversationEventsResponse {
  return {
    conversation_id: 'conv-1',
    title: 'Live test',
    user_id: 'user-1',
    user_display_name: 'User',
    active_branch: null,
    is_active: true,
    active_message_id: 'msg-1',
    created_at: '2026-07-13T00:00:00',
    updated_at: '2026-07-13T00:00:00',
    messages: [],
  };
}

describe('appendAdminLiveEvent', () => {
  test('creates an ephemeral message group and then appends semantic events', () => {
    const withInput = appendAdminLiveEvent(snapshot(), 'msg-1', {
      type: 'user_input',
      timestamp: '2026-07-13T00:00:01',
      agent: 'lead_agent',
      data: { content: 'hello' },
    }, -1);

    const withTool = appendAdminLiveEvent(withInput, 'msg-1', {
      type: 'tool_start',
      timestamp: '2026-07-13T00:00:02',
      agent: 'lead_agent',
      data: { tool: 'fetch' },
    }, -2);

    expect(withTool.messages).toHaveLength(1);
    expect(withTool.messages[0].user_input).toBe('hello');
    expect(withTool.messages[0].events.map((event) => event.event_type)).toEqual([
      'user_input',
      'tool_start',
    ]);
    expect(withTool.messages[0].events[1].id).toBe(-2);
  });

  test('ignores high-frequency and artifact projection events', () => {
    const original = snapshot();
    const next = appendAdminLiveEvent(original, 'msg-1', {
      type: 'llm_chunk',
      timestamp: '2026-07-13T00:00:01',
      data: { content: 'partial' },
    }, -1);

    expect(next).toBe(original);
  });
});

describe('isAdminMessageOffActiveBranch', () => {
  test('never marks the current live message as a side branch', () => {
    const activePathIds = new Set(['persisted-parent']);

    expect(isAdminMessageOffActiveBranch(
      'msg-live',
      'msg-live',
      true,
      activePathIds,
    )).toBe(false);
  });

  test('still marks a persisted message outside the active path', () => {
    const activePathIds = new Set(['active-leaf', 'active-parent']);

    expect(isAdminMessageOffActiveBranch(
      'old-side-branch',
      'msg-live',
      true,
      activePathIds,
    )).toBe(true);
  });
});

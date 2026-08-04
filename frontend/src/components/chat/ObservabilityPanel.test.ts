import { describe, expect, test } from 'vitest';
import type { AdminMessageGroup } from '@/lib/api';
import { aggregateStats } from './ObservabilityPanel';

describe('aggregateStats', () => {
  test('sums reported cached input while preserving explicit zero reports', () => {
    const messages: AdminMessageGroup[] = [{
      message_id: 'msg-1',
      parent_id: null,
      user_input: 'hello',
      response: 'world',
      created_at: '2026-08-04T00:00:00',
      execution_metrics: null,
      uploaded_files: null,
      events: [
        {
          id: 1,
          event_id: 'ev-1',
          event_type: 'llm_complete',
          agent_name: 'lead_agent',
          data: {
            token_usage: {
              input_tokens: 100,
              cached_input_tokens: 0,
              output_tokens: 20,
            },
          },
          created_at: '2026-08-04T00:00:01',
        },
        {
          id: 2,
          event_id: 'ev-2',
          event_type: 'llm_complete',
          agent_name: 'lead_agent',
          data: {
            token_usage: {
              input_tokens: 200,
              cached_input_tokens: 150,
              output_tokens: 30,
            },
          },
          created_at: '2026-08-04T00:00:02',
        },
        {
          id: 3,
          event_id: 'ev-3',
          event_type: 'llm_complete',
          agent_name: 'lead_agent',
          data: {
            token_usage: { input_tokens: 50, output_tokens: 10 },
          },
          created_at: '2026-08-04T00:00:03',
        },
      ],
    }];

    const stats = aggregateStats(messages);

    expect(stats.inputTokens).toBe(350);
    expect(stats.cachedInputTokens).toBe(150);
    expect(stats.cacheReportedCalls).toBe(2);
    expect(stats.outputTokens).toBe(60);
  });
});

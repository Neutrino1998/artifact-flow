import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import AssistantMessage from './AssistantMessage';

describe('AssistantMessage timestamp', () => {
  it('shows the completion time in the hover action bar', () => {
    const html = renderToStaticMarkup(
      <AssistantMessage
        content="done"
        messageId="msg-1"
        executionMetrics={{
          completed_at: '2026-08-07T08:31:00Z',
          total_duration_ms: 60_000,
        }}
      />,
    );

    expect(html).toContain('回答完成时间：');
    expect(html).toContain('<time');
  });
});

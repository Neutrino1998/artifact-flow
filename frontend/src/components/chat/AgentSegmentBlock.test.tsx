import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import type { ExecutionSegment } from '@/stores/streamStore';
import AgentSegmentBlock from './AgentSegmentBlock';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('AgentSegmentBlock', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test('renders native tool-round content exactly once', async () => {
    const content = 'I will inspect the workspace.';
    const segment: ExecutionSegment = {
      id: 'lead-1',
      agent: 'lead_agent',
      status: 'running',
      reasoningContent: '',
      llmStreamChannel: null,
      content,
      toolCallProgress: [],
      toolCalls: [{
        id: 'call-bash',
        toolName: 'bash',
        params: { command: 'pwd' },
        agent: 'lead_agent',
        status: 'running',
      }],
    };

    await act(async () => {
      root.render(
        <AgentSegmentBlock
          segment={segment}
          isActive
          defaultExpanded
        />,
      );
    });

    expect(container.textContent?.split(content)).toHaveLength(2);
    expect(container.textContent).toContain('bash');
    expect(container.querySelector('.streaming-cursor')).toBeNull();
  });

  test('renders bounded tool-call generation progress without partial arguments', async () => {
    const segment: ExecutionSegment = {
      id: 'lead-progress',
      agent: 'lead_agent',
      status: 'running',
      reasoningContent: '',
      llmStreamChannel: null,
      content: '继续更新报告的其他关键部分：',
      toolCalls: [],
      toolCallProgress: [{
        index: 0,
        callId: 'call-update',
        toolName: 'update_artifact',
        argumentsChars: 18432,
        status: 'generating',
      }],
    };

    await act(async () => {
      root.render(
        <AgentSegmentBlock
          segment={segment}
          isActive
          defaultExpanded
        />,
      );
    });

    expect(container.textContent).toContain('Preparing');
    expect(container.textContent).toContain('update_artifact');
    expect(container.textContent).toContain('18.4k chars');
    expect(container.textContent).not.toContain('{');
    expect(container.querySelector('.streaming-cursor')).toBeNull();
  });

  test('shows the cursor only while ordinary model content is streaming', async () => {
    const segment: ExecutionSegment = {
      id: 'lead-content',
      agent: 'lead_agent',
      status: 'running',
      reasoningContent: '',
      llmStreamChannel: 'content',
      content: 'Still writing',
      toolCalls: [],
      toolCallProgress: [],
    };

    await act(async () => {
      root.render(
        <AgentSegmentBlock
          segment={segment}
          isActive
          defaultExpanded
        />,
      );
    });

    const cursorHost = container.querySelector('.streaming-cursor');
    expect(cursorHost).not.toBeNull();
    expect(cursorHost?.lastElementChild?.tagName).toBe('P');
  });

  test('stops the cursor when the LLM completes but the agent remains active', async () => {
    const segment: ExecutionSegment = {
      id: 'lead-compacting',
      agent: 'lead_agent',
      status: 'running',
      reasoningContent: '',
      llmStreamChannel: null,
      content: 'Finished response',
      toolCalls: [],
      toolCallProgress: [],
    };

    await act(async () => {
      root.render(
        <AgentSegmentBlock
          segment={segment}
          isActive
          defaultExpanded
        />,
      );
    });

    expect(container.querySelector('.streaming-cursor')).toBeNull();
  });

  test.each([
    ['nested list', '- parent\n  - child'],
    ['loose list', '- first paragraph\n\n  second paragraph'],
    ['quoted list', '> - final item'],
  ])('omits best-effort cursor placement for a %s', async (_kind, content) => {
    const segment: ExecutionSegment = {
      id: `lead-${_kind}`,
      agent: 'lead_agent',
      status: 'running',
      reasoningContent: '',
      llmStreamChannel: 'content',
      content,
      toolCalls: [],
      toolCallProgress: [],
    };

    await act(async () => {
      root.render(
        <AgentSegmentBlock
          segment={segment}
          isActive
          defaultExpanded
        />,
      );
    });

    expect(container.querySelector(
      '.streaming-cursor > :where(p, h1, h2, h3, h4, h5, h6):last-child',
    )).toBeNull();
  });

  test('shows cached input as a parenthesized subset of input tokens', async () => {
    const segment: ExecutionSegment = {
      id: 'lead-cached',
      agent: 'lead_agent',
      status: 'complete',
      reasoningContent: 'done',
      llmStreamChannel: null,
      content: '',
      toolCalls: [],
      toolCallProgress: [],
      tokenUsage: {
        input_tokens: 12_400,
        cached_input_tokens: 8_200,
        output_tokens: 600,
        total_tokens: 13_000,
      },
    };

    await act(async () => {
      root.render(
        <AgentSegmentBlock
          segment={segment}
          isActive={false}
          defaultExpanded={false}
        />,
      );
    });

    expect(container.textContent).toContain('12K ↑ (8.2K ↻) · 600 ↓');
  });
});

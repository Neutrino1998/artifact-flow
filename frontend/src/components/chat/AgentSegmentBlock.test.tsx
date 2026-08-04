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
      isThinking: false,
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
  });

  test('renders bounded tool-call generation progress without partial arguments', async () => {
    const segment: ExecutionSegment = {
      id: 'lead-progress',
      agent: 'lead_agent',
      status: 'running',
      reasoningContent: '',
      isThinking: false,
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
  });
});

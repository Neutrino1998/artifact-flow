import type { MessageEventItem } from '@/lib/api';
import type { ExecutionSegment, ToolCallInfo, NonAgentBlock, CompactionBlock } from '@/stores/streamStore';
import type { TokenUsage } from '@/types/events';

/**
 * Reconstruct ExecutionSegment[] from persisted MessageEvent records.
 * Mirrors the SSE event handling logic in useSSE.ts.
 */
export function reconstructSegments(events: MessageEventItem[]): ExecutionSegment[] {
  const segments: ExecutionSegment[] = [];

  function current(): ExecutionSegment | undefined {
    return segments[segments.length - 1];
  }

  for (const evt of events) {
    const { event_type, agent_name, data } = evt;

    switch (event_type) {
      case 'agent_start': {
        segments.push({
          id: `${agent_name ?? 'Agent'}-${evt.created_at}`,
          agent: agent_name ?? 'Agent',
          status: 'running',
          reasoningContent: '',
          isThinking: false,
          toolCalls: [],
          content: '',
          llmOutput: '',
        });
        break;
      }

      case 'llm_complete': {
        const seg = current();
        if (!seg) break;
        const content = (data?.content as string) ?? '';
        seg.content = content;
        const reasoning = (data?.reasoning_content as string) ?? '';
        if (reasoning) {
          seg.reasoningContent = reasoning;
          seg.isThinking = false; // historical — already complete
        }
        if (content.includes('<tool_call>') && !seg.llmOutput) {
          seg.llmOutput = content;
        }
        const tokenUsage = data?.token_usage as ExecutionSegment['tokenUsage'];
        if (tokenUsage) seg.tokenUsage = tokenUsage;
        const model = data?.model as string | undefined;
        if (model) seg.model = model;
        const durationMs = data?.duration_ms as number | undefined;
        if (durationMs != null) seg.llmDurationMs = durationMs;
        break;
      }

      case 'tool_start': {
        const seg = current();
        if (!seg) break;
        const toolName = (data?.tool as string) ?? '';
        // Preserve LLM output before clearing content
        if (seg.content && !seg.llmOutput) {
          seg.llmOutput = seg.content;
        }
        seg.toolCalls.push({
          id: `${toolName}-${evt.created_at}`,
          toolName,
          params: (data?.params as Record<string, unknown>) ?? {},
          agent: agent_name ?? '',
          status: 'running',
        });
        seg.content = '';
        break;
      }

      case 'tool_complete': {
        const toolName = (data?.tool as string) ?? '';
        const success = (data?.success as boolean) ?? true;
        const result = typeof data?.result_data === 'string'
          ? data.result_data as string
          : !success && typeof data?.error === 'string'
            ? data.error as string
            : JSON.stringify(data?.result_data ?? '');
        const durationMs = data?.duration_ms as number | undefined;

        // Find the matching running tool call across all segments
        let found = false;
        for (const seg of segments) {
          const tc = seg.toolCalls.find(
            (t) => t.toolName === toolName && t.status === 'running'
          );
          if (tc) {
            tc.status = success ? 'success' : 'error';
            tc.result = result;
            tc.durationMs = durationMs;
            found = true;
            break;
          }
        }
        if (!found) {
          // Orphan tool_complete — append to current segment
          const seg = current();
          if (seg) {
            seg.toolCalls.push({
              id: `${toolName}-${evt.created_at}`,
              toolName,
              params: (data?.params as Record<string, unknown>) ?? {},
              agent: agent_name ?? '',
              status: success ? 'success' : 'error',
              result,
              durationMs,
            });
          }
        }
        break;
      }

      case 'agent_complete': {
        const seg = current();
        if (seg) seg.status = 'complete';
        break;
      }

      // Skip non-visual events (metadata, complete, error, permission_*, etc.)
      default:
        break;
    }
  }

  // Mark any remaining running segments as complete
  for (const seg of segments) {
    if (seg.status === 'running') seg.status = 'complete';
  }

  // Only return segments that have meaningful content (tool calls or reasoning)
  return segments.filter(
    (seg) => seg.toolCalls.length > 0 || seg.reasoningContent
  );
}

/**
 * Reconstruct NonAgentBlock[] from persisted MessageEvent records.
 *
 * Compaction replays from the paired compaction_start + compaction_summary
 * events. They're matched by arrival order within a message (same strategy as
 * the live SSE handler): each compaction_summary consumes the earliest
 * still-running compaction block of the same position bucket.
 */
export function reconstructNonAgentBlocks(events: MessageEventItem[]): NonAgentBlock[] {
  const blocks: NonAgentBlock[] = [];
  let agentSegmentCount = 0;

  for (const evt of events) {
    const { event_type, data } = evt;

    if (event_type === 'agent_start') {
      agentSegmentCount++;
    } else if (event_type === 'queued_message') {
      blocks.push({
        kind: 'inject',
        id: `inject-${evt.created_at}`,
        content: (data?.content as string) ?? '',
        timestamp: evt.created_at,
        position: agentSegmentCount,
      });
    } else if (event_type === 'compaction_start') {
      blocks.push({
        kind: 'compaction',
        id: `compact-${evt.created_at}`,
        state: 'running',
        triggerTokens: data ? {
          input: (data.last_input_tokens as number) ?? 0,
          output: (data.last_output_tokens as number) ?? 0,
        } : undefined,
        timestamp: evt.created_at,
        position: agentSegmentCount,
      });
    } else if (event_type === 'compaction_summary') {
      // Find the most recent still-running compaction block and finalize it
      for (let i = blocks.length - 1; i >= 0; i--) {
        const b = blocks[i];
        if (b.kind === 'compaction' && b.state === 'running') {
          const err = (data?.error as string | null | undefined) ?? null;
          const patched: CompactionBlock = {
            ...b,
            state: err ? 'error' : 'done',
            summary: (data?.content as string) ?? '',
            model: (data?.model as string | undefined) ?? undefined,
            tokenUsage: (data?.token_usage as TokenUsage | undefined) ?? undefined,
            durationMs: (data?.duration_ms as number | undefined) ?? undefined,
            error: err,
          };
          blocks[i] = patched;
          break;
        }
      }
    }
  }

  return blocks;
}

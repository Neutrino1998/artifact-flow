import type { MessageEventItem } from '@/lib/api';
import type { ExecutionSegment, ToolCallInfo, NonAgentBlock, CompactionBlock } from '@/stores/streamStore';
import type { TokenUsage, LLMCompleteData } from '@/types/events';

/**
 * Reconstruct ExecutionSegment[] from persisted MessageEvent records (history reload).
 *
 * TWIN of the live SSE reducer in useSSE.ts: both fold the SAME events into the SAME
 * ExecutionSegment / ToolCallInfo shapes. Per-event field mapping is duplicated, NOT
 * shared — the orchestration genuinely differs (batch fold here vs streaming + llm_chunk
 * there), so the two loops stay separate by design. Add/remove a field on a UI object in
 * EITHER file → mirror it in the other. (The `reason` field once shipped live-only because
 * this side was missed on reload.)
 */
export function reconstructSegments(events: MessageEventItem[]): ExecutionSegment[] {
  const segments: ExecutionSegment[] = [];
  // Latched on permission_result, consumed by the next tool_start. Mirrors
  // useSSE._pendingPermissionResult — engine emits permission_result
  // immediately before the relevant tool_start, so serial pairing is correct.
  let pendingPermission: { approved: boolean; reason?: string } | null = null;

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
          toolCallProgress: [],
          content: '',
        });
        break;
      }

      case 'llm_complete': {
        const seg = current();
        if (!seg) break;
        const d = (data ?? {}) as Partial<LLMCompleteData>;
        const content = d.content ?? '';
        seg.content = content;
        if (d.reasoning_content) {
          seg.reasoningContent = d.reasoning_content;
          seg.isThinking = false; // historical — already complete
        }
        if (d.token_usage) seg.tokenUsage = d.token_usage;
        if (d.model) seg.model = d.model;
        if (d.duration_ms != null) seg.llmDurationMs = d.duration_ms;
        break;
      }

      case 'tool_start': {
        // Lane by agent (TWIN of useSSE TOOL_START): with in-place subagent
        // recursion the caller's later tools arrive AFTER the subagent's
        // segment — current() would misfile them onto the subagent's lane.
        // Fall back to current() when no lane matches (agent missing).
        const lane = agent_name ?? 'Agent';
        let seg: ExecutionSegment | undefined;
        for (let i = segments.length - 1; i >= 0; i--) {
          if (segments[i].agent === lane) {
            seg = segments[i];
            break;
          }
        }
        if (!seg) seg = current();
        if (!seg) break;
        const callId = data?.call_id as string | undefined;
        const toolName = (data?.tool as string) ?? '';
        if (!callId) {
          console.error('[reconstructSegments] tool_start missing native call_id');
          break;
        }
        const permission = pendingPermission ?? undefined;
        pendingPermission = null;
        // TWIN: keep this field set identical to useSSE.ts TOOL_START. `reason`
        // is an optional backend-supplied display explanation and must survive
        // reload; it is not the model's reasoning channel.
        const reason = data?.reason as string | undefined;
        seg.toolCalls.push({
          id: callId,
          toolName,
          params: (data?.params as Record<string, unknown>) ?? {},
          agent: agent_name ?? '',
          status: 'running',
          ...(reason ? { reason } : {}),
          ...(permission ? { permission } : {}),
        });
        break;
      }

      case 'permission_result': {
        const approved = (data?.approved as boolean) ?? false;
        const reason = data?.reason as string | undefined;
        pendingPermission = reason ? { approved, reason } : { approved };
        break;
      }

      case 'tool_complete': {
        const callId = data?.call_id as string | undefined;
        const toolName = (data?.tool as string) ?? '';
        const success = (data?.success as boolean) ?? true;
        const result = typeof data?.result_data === 'string'
          ? data.result_data as string
          : !success && typeof data?.error === 'string'
            ? data.error as string
            : JSON.stringify(data?.result_data ?? '');
        const durationMs = data?.duration_ms as number | undefined;

        // Native call_id, not the function name, is the structural join key.
        // If no running call matches, the producer contract is broken upstream.
        let matched = false;
        for (const seg of segments) {
          const tc = seg.toolCalls.find(
            (t) => t.id === callId && t.status === 'running'
          );
          if (tc) {
            tc.status = success ? 'success' : 'error';
            tc.result = result;
            tc.durationMs = durationMs;
            matched = true;
            break;
          }
        }
        if (!callId || !matched) {
          console.error(
            `[reconstructSegments] tool_complete for "${toolName}" has no matching running call_id`
          );
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
    } else if (event_type === 'error') {
      // Replay path only — live error is rendered standalone via streamStore.error.
      // Engine emits {error: string, agent?: string} (engine.py:307,576,610,647).
      blocks.push({
        kind: 'error',
        id: `error-${evt.created_at}`,
        error: (data?.error as string) ?? 'Unknown error',
        // 持久化的可回传定位码(live 与 replay 一致;旧数据可能缺省)。
        requestId: (data?.request_id as string | undefined) || undefined,
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

import type { AdminEventItem, AdminMessageGroup } from '@/lib/api';
import type { NativeToolCall } from '@/types/events';

function compactText(value: unknown, max = 96): string {
  if (typeof value !== 'string') return '';
  const oneLine = value.replace(/\s+/g, ' ').trim();
  return oneLine.length > max ? `${oneLine.slice(0, max)}...` : oneLine;
}

export function nativeToolCalls(
  data: Record<string, unknown> | null,
): NativeToolCall[] {
  return Array.isArray(data?.tool_calls)
    ? data.tool_calls as NativeToolCall[]
    : [];
}

export function formatNativeToolCalls(calls: NativeToolCall[]): string {
  const diagnostic = calls.map((call) => {
    let parsedArguments: unknown;
    try {
      parsedArguments = JSON.parse(call.function.arguments);
    } catch {
      parsedArguments = undefined;
    }
    return {
      id: call.id,
      type: call.type,
      function: call.function,
      ...(parsedArguments !== undefined ? { parsed_arguments: parsedArguments } : {}),
    };
  });
  return JSON.stringify(diagnostic, null, 2);
}

export function isToolFailure(event: AdminEventItem): boolean {
  return event.event_type === 'tool_complete' && event.data?.success === false;
}

export function isPermissionDenied(event: AdminEventItem): boolean {
  return event.event_type === 'permission_result' && event.data?.approved === false;
}

function isTerminalIssue(event: AdminEventItem): boolean {
  return event.event_type === 'error'
    || event.event_type === 'timed_out'
    || event.event_type === 'cancelled';
}

export function isFailedCompaction(event: AdminEventItem): boolean {
  return event.event_type === 'compaction_summary' && event.data?.success === false;
}

export function isIssueEvent(event: AdminEventItem): boolean {
  return isTerminalIssue(event)
    || isToolFailure(event)
    || isPermissionDenied(event)
    || isFailedCompaction(event);
}

export function eventIssueTone(
  event: AdminEventItem,
): 'error' | 'warning' | null {
  if (event.event_type === 'error') return 'error';
  if (
    isToolFailure(event)
    || event.event_type === 'timed_out'
    || event.event_type === 'cancelled'
    || isPermissionDenied(event)
    || isFailedCompaction(event)
  ) {
    return 'warning';
  }
  return null;
}

export function eventSummary(event: AdminEventItem): string {
  const data = event.data;
  if (!data) return '';

  switch (event.event_type) {
    case 'llm_complete': {
      const tokens = data.token_usage as Record<string, number> | undefined;
      const model = (data.model as string) || '';
      const duration = data.duration_ms as number | undefined;
      const cached = tokens?.cached_input_tokens;
      const cacheSummary = cached != null ? ` | ${cached} ↻ cached` : '';
      const calls = nativeToolCalls(data);
      const callSummary = calls.length > 0
        ? ` | ${calls.length} call${calls.length === 1 ? '' : 's'}: ${calls.map((call) => call.function.name).join(', ')}`
        : '';
      return `${model} | ${tokens?.input_tokens ?? 0}/${tokens?.output_tokens ?? 0} tokens${cacheSummary} | ${duration ?? 0}ms${callSummary}`;
    }
    case 'tool_start':
      return `${data.tool as string}`;
    case 'tool_complete': {
      const ok = data.success as boolean;
      const duration = data.duration_ms as number | undefined;
      const error = compactText(data.error, 90);
      return `${data.tool as string} ${ok ? 'OK' : 'FAIL'} ${duration ?? 0}ms${!ok && error ? ` | ${error}` : ''}`;
    }
    case 'agent_start': {
      const model = data.model as string | undefined;
      return `${data.agent as string}${model ? ` | ${model}` : ''}`;
    }
    case 'agent_complete':
      return `${data.agent as string} done`;
    case 'error':
      return (data.error as string)?.slice(0, 80) || 'error';
    case 'permission_request':
      return `${data.tool as string} (${data.permission_level as string})`;
    case 'permission_result':
      return data.approved
        ? 'approved'
        : `denied${data.reason ? ` | ${data.reason as string}` : ''}`;
    case 'timed_out':
      return 'execution timed out';
    case 'cancelled':
      return (data.reason as string) || (data.response as string) || 'cancelled';
    case 'user_input':
      return (data.content as string)?.slice(0, 60) || '';
    default:
      return '';
  }
}

export function formatLlmTokenUsage(tokens: Record<string, number>): string {
  const cached = tokens.cached_input_tokens != null
    ? ` | cached: ${tokens.cached_input_tokens} ↻`
    : '';
  return `in: ${tokens.input_tokens ?? 0}${cached} | out: ${tokens.output_tokens ?? 0}`;
}

export interface AggregatedStats {
  inputTokens: number;
  cachedInputTokens: number;
  cacheReportedCalls: number;
  outputTokens: number;
  llmCalls: number;
  toolCalls: number;
  toolFails: number;
  terminalErrors: number;
  timedOut: number;
  cancelled: number;
  permissionDenied: number;
  compactionFails: number;
  issueEvents: number;
  totalDurationMs: number;
}

export function aggregateStats(messages: AdminMessageGroup[]): AggregatedStats {
  const stats: AggregatedStats = {
    inputTokens: 0,
    cachedInputTokens: 0,
    cacheReportedCalls: 0,
    outputTokens: 0,
    llmCalls: 0,
    toolCalls: 0,
    toolFails: 0,
    terminalErrors: 0,
    timedOut: 0,
    cancelled: 0,
    permissionDenied: 0,
    compactionFails: 0,
    issueEvents: 0,
    totalDurationMs: 0,
  };

  for (const message of messages) {
    const metrics = message.execution_metrics as Record<string, number> | null;
    if (metrics?.total_duration_ms) {
      stats.totalDurationMs += metrics.total_duration_ms;
    }
    for (const event of message.events) {
      const data = event.data;
      if (isIssueEvent(event)) stats.issueEvents++;
      if (event.event_type === 'error') stats.terminalErrors++;
      if (event.event_type === 'timed_out') stats.timedOut++;
      if (event.event_type === 'cancelled') stats.cancelled++;
      if (isFailedCompaction(event)) stats.compactionFails++;
      if (!data) continue;

      if (event.event_type === 'llm_complete') {
        stats.llmCalls++;
        const tokens = data.token_usage as Record<string, number> | undefined;
        if (tokens) {
          stats.inputTokens += tokens.input_tokens ?? 0;
          stats.outputTokens += tokens.output_tokens ?? 0;
          if (tokens.cached_input_tokens != null) {
            stats.cachedInputTokens += tokens.cached_input_tokens;
            stats.cacheReportedCalls++;
          }
        }
      } else if (event.event_type === 'tool_complete') {
        stats.toolCalls++;
        if (!(data.success as boolean)) stats.toolFails++;
      } else if (
        event.event_type === 'permission_result'
        && data.approved === false
      ) {
        stats.permissionDenied++;
      }
    }
  }
  return stats;
}

export function formatNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

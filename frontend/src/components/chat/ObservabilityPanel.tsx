'use client';

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import type { ReactNode } from 'react';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { CopyIcon } from '@/components/ui/CopyIcon';
import { FeedbackRatingIcon } from '@/components/ui/FeedbackRatingIcon';
import { PillBadge } from '@/components/ui/PillBadge';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import { BUTTON_SECONDARY, SELECT_COMPACT, MENU_ROW_HOVER } from '@/lib/styles';
import { SELECT_CHEVRON_COMPACT } from '@/components/ui/SelectChevron';
import * as api from '@/lib/api';
import { isCsvMime } from '@/lib/artifactPreview';
import { parseUtcIso } from '@/lib/time';
import { formatDuration } from '@/lib/formatDuration';
import { formatCachedTokens, formatTokens } from '@/lib/formatTokens';
import {
  getTextArtifactDownloadFilename,
  triggerBlobDownload,
  triggerObjectUrlDownload,
} from '@/lib/download';
import ArtifactPreviewContent from '@/components/artifact/ArtifactPreviewContent';
import PanelSearchBar from './PanelSearchBar';
import Pagination from './Pagination';
import type {
  AdminConversationSummary,
  AdminFeedbackItem,
  AdminMessageGroup,
  AdminEventItem,
  AdminConversationEventsResponse,
} from '@/lib/api';
import { FEEDBACK_TAG_LABELS } from '@/lib/messageFeedback';
import type { ArtifactSummary, ArtifactDetail, VersionDetail } from '@/types';
import type { NativeToolCall } from '@/types/events';
import { useUIStore } from '@/stores/uiStore';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { connectSSE } from '@/lib/sse';
import {
  ADMIN_TERMINAL_EVENTS,
  appendAdminLiveEvent,
  formatAdminInputPreview,
  isAdminMessageOffActiveBranch,
} from '@/lib/adminLiveEvents';

const DEFAULT_PAGE_SIZE = 20;

function messageAnchorId(messageId: string): string {
  return `admin-message-${encodeURIComponent(messageId)}`;
}

// ── Event type colors ──
// Categorical palette via the scoped `trace` tokens; agent_* shares accent
// (agent activity = brand hue, same reasoning as status.running).
function eventColor(event: AdminEventItem): string {
  const type = event.event_type;
  const tone = eventIssueTone(event);
  if (tone === 'error') return 'text-status-error';
  if (tone === 'warning') return 'text-status-warning';
  if (type === 'error') return 'text-status-error';
  if (type.startsWith('permission')) return 'text-status-warning';
  if (type === 'llm_complete') return 'text-trace-llm dark:text-trace-llm-dark';
  if (type.startsWith('tool_')) return 'text-trace-tool dark:text-trace-tool-dark';
  if (type.startsWith('agent_')) return 'text-accent';
  return 'text-text-tertiary dark:text-text-tertiary-dark';
}

function compactText(value: unknown, max = 96): string {
  if (typeof value !== 'string') return '';
  const oneLine = value.replace(/\s+/g, ' ').trim();
  return oneLine.length > max ? `${oneLine.slice(0, max)}...` : oneLine;
}

function nativeToolCalls(data: Record<string, unknown> | null): NativeToolCall[] {
  return Array.isArray(data?.tool_calls) ? data.tool_calls as NativeToolCall[] : [];
}

function formatNativeToolCalls(calls: NativeToolCall[]): string {
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

function isToolFailure(event: AdminEventItem): boolean {
  return event.event_type === 'tool_complete' && event.data?.success === false;
}

function isPermissionDenied(event: AdminEventItem): boolean {
  return event.event_type === 'permission_result' && event.data?.approved === false;
}

function isTerminalIssue(event: AdminEventItem): boolean {
  return event.event_type === 'error' || event.event_type === 'timed_out' || event.event_type === 'cancelled';
}

function isFailedCompaction(event: AdminEventItem): boolean {
  return event.event_type === 'compaction_summary' && event.data?.success === false;
}

function isIssueEvent(event: AdminEventItem): boolean {
  return isTerminalIssue(event) || isToolFailure(event) || isPermissionDenied(event) || isFailedCompaction(event);
}

function eventIssueTone(event: AdminEventItem): 'error' | 'warning' | null {
  if (event.event_type === 'error') return 'error';
  if (isToolFailure(event) || event.event_type === 'timed_out' || event.event_type === 'cancelled' || isPermissionDenied(event) || isFailedCompaction(event)) {
    return 'warning';
  }
  return null;
}

function eventSummary(event: AdminEventItem): string {
  const d = event.data;
  if (!d) return '';
  switch (event.event_type) {
    case 'llm_complete': {
      const tokens = d.token_usage as Record<string, number> | undefined;
      const model = (d.model as string) || '';
      const dur = d.duration_ms as number | undefined;
      const cached = tokens?.cached_input_tokens;
      const cacheSummary = cached != null ? ` | ${cached} ↻ cached` : '';
      const calls = nativeToolCalls(d);
      const callSummary = calls.length > 0
        ? ` | ${calls.length} call${calls.length === 1 ? '' : 's'}: ${calls.map((call) => call.function.name).join(', ')}`
        : '';
      return `${model} | ${tokens?.input_tokens ?? 0}/${tokens?.output_tokens ?? 0} tokens${cacheSummary} | ${dur ?? 0}ms${callSummary}`;
    }
    case 'tool_start':
      return `${d.tool as string}`;
    case 'tool_complete': {
      const ok = d.success as boolean;
      const dur = d.duration_ms as number | undefined;
      const err = compactText(d.error, 90);
      return `${d.tool as string} ${ok ? 'OK' : 'FAIL'} ${dur ?? 0}ms${!ok && err ? ` | ${err}` : ''}`;
    }
    case 'agent_start': {
      const model = d.model as string | undefined;
      return `${d.agent as string}${model ? ` | ${model}` : ''}`;
    }
    case 'agent_complete':
      return `${d.agent as string} done`;
    case 'error':
      return (d.error as string)?.slice(0, 80) || 'error';
    case 'permission_request':
      return `${d.tool as string} (${d.permission_level as string})`;
    case 'permission_result':
      return d.approved ? 'approved' : `denied${d.reason ? ` | ${d.reason as string}` : ''}`;
    case 'timed_out':
      return 'execution timed out';
    case 'cancelled':
      return (d.reason as string) || (d.response as string) || 'cancelled';
    case 'user_input':
      return (d.content as string)?.slice(0, 60) || '';
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

function formatTime(iso: string): string {
  try {
    const d = parseUtcIso(iso);
    return d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

// ── Stats helpers ──
interface AggregatedStats {
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
  for (const msg of messages) {
    const metrics = msg.execution_metrics as Record<string, number> | null;
    if (metrics?.total_duration_ms) stats.totalDurationMs += metrics.total_duration_ms;
    for (const ev of msg.events) {
      const d = ev.data;
      if (isIssueEvent(ev)) stats.issueEvents++;
      if (ev.event_type === 'error') stats.terminalErrors++;
      if (ev.event_type === 'timed_out') stats.timedOut++;
      if (ev.event_type === 'cancelled') stats.cancelled++;
      if (isFailedCompaction(ev)) stats.compactionFails++;
      if (!d) continue;
      if (ev.event_type === 'llm_complete') {
        stats.llmCalls++;
        const tokens = d.token_usage as Record<string, number> | undefined;
        if (tokens) {
          stats.inputTokens += tokens.input_tokens ?? 0;
          stats.outputTokens += tokens.output_tokens ?? 0;
          if (tokens.cached_input_tokens != null) {
            stats.cachedInputTokens += tokens.cached_input_tokens;
            stats.cacheReportedCalls++;
          }
        }
      } else if (ev.event_type === 'tool_complete') {
        stats.toolCalls++;
        if (!(d.success as boolean)) stats.toolFails++;
      } else if (ev.event_type === 'permission_result' && d.approved === false) {
        stats.permissionDenied++;
      }
    }
  }
  return stats;
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function StatCard({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'neutral' | 'warning' | 'error' }) {
  const toneClass =
    tone === 'error'
      ? 'bg-status-error/10 border border-status-error/30'
      : tone === 'warning'
        ? 'bg-status-warning/10 border border-status-warning/30'
        : 'bg-panel-accent dark:bg-surface-dark';
  return (
    <div className={`px-3 py-1.5 rounded-lg ${toneClass}`}>
      <div className="text-[10px] text-text-tertiary dark:text-text-tertiary-dark uppercase tracking-wide">{label}</div>
      <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">{value}</div>
    </div>
  );
}

function IssueSummaryBar({
  stats,
  issuesOnly,
  onToggle,
}: {
  stats: AggregatedStats;
  issuesOnly: boolean;
  onToggle: () => void;
}) {
  if (stats.issueEvents === 0) return null;
  const tone = stats.terminalErrors > 0 ? 'error' : 'warning';
  const bits: string[] = [];
  if (stats.terminalErrors > 0) bits.push(`${stats.terminalErrors} error`);
  if (stats.timedOut > 0) bits.push(`${stats.timedOut} timeout`);
  if (stats.cancelled > 0) bits.push(`${stats.cancelled} cancelled`);
  if (stats.toolFails > 0) bits.push(`${stats.toolFails} tool fail`);
  if (stats.permissionDenied > 0) bits.push(`${stats.permissionDenied} permission denied`);
  if (stats.compactionFails > 0) bits.push(`${stats.compactionFails} compaction fail`);
  return (
    <div className={`mx-4 mt-3 px-3 py-2 rounded-lg border flex items-center gap-3 text-xs ${
      tone === 'error'
        ? 'bg-status-error/10 border-status-error/30 text-status-error'
        : 'bg-status-warning/10 border-status-warning/30 text-status-warning'
    }`}>
      <span className="font-medium">
        发现 {stats.issueEvents} 个异常信号
      </span>
      <span className="text-text-secondary dark:text-text-secondary-dark truncate">
        {bits.join(' · ')}
      </span>
      <button
        type="button"
        onClick={onToggle}
        className="ml-auto shrink-0 px-2 py-0.5 rounded-md border bg-surface dark:bg-bg-dark border-current transition-colors"
      >
        {issuesOnly ? '显示全部' : '只看异常'}
      </button>
    </div>
  );
}

function formatDateTime(iso: string): string {
  try {
    return parseUtcIso(iso).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

// Copyable id (conv id / active branch) — swaps to a checkmark on copy, like
// the chat copy button. `mono` shows the value verbatim for ids.
function CopyableValue({ value, mono = false }: { value: string; mono?: boolean }) {
  const { copied, copy } = useCopyFeedback();
  return (
    <button
      type="button"
      onClick={() => copy(value)}
      title="点击复制"
      className={`inline-flex items-center gap-1 hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors max-w-full align-bottom ${mono ? 'font-mono' : ''}`}
    >
      <span className="truncate">{value}</span>
      <CopyIcon copied={copied} size={11} />
    </button>
  );
}

// One "label: value" cell in the header meta row.
function MetaItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 min-w-0">
      <span className="text-text-tertiary dark:text-text-tertiary-dark">{label}</span>
      <span className="text-text-secondary dark:text-text-secondary-dark min-w-0 truncate">{children}</span>
    </span>
  );
}

// Header metadata block: conv id + owner + branch + timestamps.
function ConvMetaBlock({ data, fallbackConvId }: {
  data: AdminConversationEventsResponse | null;
  fallbackConvId: string | null;
}) {
  const convId = data?.conversation_id || fallbackConvId;
  if (!convId) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
      <MetaItem label="ID"><CopyableValue value={convId} mono /></MetaItem>
      {data?.user_display_name && (
        <MetaItem label="属主">{data.user_display_name}</MetaItem>
      )}
      {data?.active_branch && (
        <MetaItem label="活跃分支"><CopyableValue value={data.active_branch} mono /></MetaItem>
      )}
      {data?.created_at && (
        <MetaItem label="创建">{formatDateTime(data.created_at)}</MetaItem>
      )}
      {data?.updated_at && (
        <MetaItem label="更新">{formatDateTime(data.updated_at)}</MetaItem>
      )}
    </div>
  );
}

// ── Main Panel ──
export default function ObservabilityPanel() {
  const selectedConvId = useUIStore((s) => s.observabilitySelectedConvId);
  const browser = useUIStore((s) => s.observabilityBrowser);
  const focusMessageId = useUIStore((s) => s.observabilityFocusMessageId);
  const setObservabilityBrowser = useUIStore((s) => s.setObservabilityBrowser);
  const openObservabilityMessage = useUIStore((s) => s.openObservabilityMessage);
  const setObservabilitySelectedConvId = useUIStore((s) => s.setObservabilitySelectedConvId);

  // Timeline state
  const [eventsData, setEventsData] = useState<AdminConversationEventsResponse | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [collapsedMessages, setCollapsedMessages] = useState<Set<string>>(new Set());
  const [selectedEvent, setSelectedEvent] = useState<AdminEventItem | null>(null);
  // message_id 的选中事件所属消息 —— prompt 重建需要它（分支正确的 path 锚定）
  const [selectedMsgId, setSelectedMsgId] = useState<string | null>(null);
  const refreshTick = useUIStore((s) => s.observabilityRefreshTick);
  const [viewMode, setViewMode] = useState<'events' | 'artifacts'>('events');
  const [issuesOnly, setIssuesOnly] = useState(false);
  const handledFocusKeyRef = useRef<string | null>(null);

  useEffect(() => {
    setViewMode('events');
    setIssuesOnly(false);
  }, [selectedConvId]);

  // Fetch events when selected conversation changes or refresh is triggered
  useEffect(() => {
    if (!selectedConvId) {
      setEventsData(null);
      setSelectedEvent(null);
      setSelectedMsgId(null);
      return;
    }
    let cancelled = false;
    setEventsData(null);
    setSelectedEvent(null);
    setSelectedMsgId(null);
    setEventsLoading(true);
    api.getAdminConversationEvents(selectedConvId).then((res) => {
      if (!cancelled) {
        setEventsData(res);
        setCollapsedMessages(new Set());
      }
    }).catch((err) => {
      if (!cancelled) {
        console.error('Failed to load conversation events:', err);
        setEventsData(null);
      }
    }).finally(() => {
      if (!cancelled) setEventsLoading(false);
    });
    return () => { cancelled = true; };
  }, [selectedConvId, refreshTick]);

  useEffect(() => {
    if (browser !== 'none') handledFocusKeyRef.current = null;
  }, [browser]);

  useEffect(() => {
    if (browser !== 'none') return;
    if (!eventsData || !focusMessageId) return;
    if (!eventsData.messages.some((message) => message.message_id === focusMessageId)) return;
    const focusKey = `${selectedConvId ?? ''}:${focusMessageId}`;
    if (handledFocusKeyRef.current === focusKey) return;
    handledFocusKeyRef.current = focusKey;
    setCollapsedMessages((prev) => {
      if (!prev.has(focusMessageId)) return prev;
      const next = new Set(prev);
      next.delete(focusMessageId);
      return next;
    });
    const timer = window.setTimeout(() => {
      document.getElementById(messageAnchorId(focusMessageId))?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [browser, eventsData, focusMessageId, selectedConvId]);

  const activeMessageId = eventsData?.active_message_id ?? null;
  const activeMessageHasPersistedTerminal = useMemo(() => {
    if (!eventsData || !activeMessageId) return false;
    const group = eventsData.messages.find((message) => message.message_id === activeMessageId);
    return group?.events.some(
      (event) => event.id >= 0 && ADMIN_TERMINAL_EVENTS.has(event.event_type),
    ) ?? false;
  }, [eventsData, activeMessageId]);

  // The DB response is the durable snapshot.  While its active message is still
  // running, project semantic SSE events into that one group using temporary
  // negative ids.  A terminal event triggers a fresh DB read that replaces the
  // projection wholesale, so live/replay ids can never be confused or duplicated.
  // Unexpected disconnect is best-effort: fall back to DB; manual refresh/reopen
  // establishes a fresh from-start subscription without admin-only retry state.
  useEffect(() => {
    if (!selectedConvId || !activeMessageId || activeMessageHasPersistedTerminal) return;

    const controller = new AbortController();
    let temporaryId = -1;
    let sawTerminal = false;

    const refreshAuthoritative = async (terminalMessageId: string | null) => {
      try {
        const fresh = await api.getAdminConversationEvents(selectedConvId);
        if (controller.signal.aborted) return;

        // The terminal SSE is the execution boundary, while lease cleanup runs
        // immediately afterwards.  Avoid briefly reconnecting to the same sealed
        // message if this GET raced that cleanup.  A genuinely newer message id is
        // preserved and the effect will subscribe to it next.
        if (terminalMessageId && fresh.active_message_id === terminalMessageId) {
          setEventsData({ ...fresh, is_active: false, active_message_id: null });
        } else {
          setEventsData(fresh);
        }
        setSelectedEvent(null);
        setSelectedMsgId(null);
      } catch (err) {
        if (!controller.signal.aborted) {
          console.error('Failed to refresh admin events after live stream:', err);
        }
      }
    };

    connectSSE(
      api.getAdminConversationStreamUrl(selectedConvId),
      {
        onEvent: (event) => {
          if (controller.signal.aborted) return;
          const isTransportError = event.type === 'error'
            && event.data?.message_id !== activeMessageId;
          setEventsData((current) => {
            if (!current || current.active_message_id !== activeMessageId) return current;
            return appendAdminLiveEvent(current, activeMessageId, event, temporaryId--);
          });

          // Transport-generated errors (expired stream / lost lease) deliberately
          // have no message_id and are not execution terminals.  Only the
          // controller's ERROR for this message is authoritative terminal state.
          const isExecutionTerminal = ADMIN_TERMINAL_EVENTS.has(String(event.type))
            && !isTransportError;
          if (isExecutionTerminal) {
            sawTerminal = true;
            void refreshAuthoritative(activeMessageId);
          }
        },
        onError: (err) => {
          if (controller.signal.aborted) return;
          console.error('Admin live stream failed:', err);
          void refreshAuthoritative(null);
        },
        onClose: () => {
          if (!controller.signal.aborted && !sawTerminal) {
            void refreshAuthoritative(null);
          }
        },
      },
      controller.signal,
    );

    return () => controller.abort();
  }, [
    selectedConvId,
    activeMessageId,
    activeMessageHasPersistedTerminal,
  ]);

  const toggleMessageCollapse = useCallback((msgId: string) => {
    setCollapsedMessages((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  }, []);

  // 活动分支路径：从 active_branch(叶子)沿 parent_id 上溯到根。对话有分支时（路径
  // 未覆盖全部消息），不在路径上的消息标「旁支」，让 admin 看出分支结构 —— 否则
  // 扁平时间线把所有分支混在一起、分不清谁在当前活动线上。
  // 必须在下面的 early-return 之前调用：rules-of-hooks 要求所有 hook 无条件先于任何
  // return。eventsData 为空时返回空集，提前计算无副作用。
  const activePathIds = useMemo(() => {
    const ids = new Set<string>();
    if (!eventsData) return ids;
    const parentOf = new Map<string, string | null>();
    for (const m of eventsData.messages) parentOf.set(m.message_id, m.parent_id);
    let cur: string | null | undefined = eventsData.active_branch;
    while (cur != null && parentOf.has(cur) && !ids.has(cur)) {
      ids.add(cur);
      cur = parentOf.get(cur) ?? null;
    }
    return ids;
  }, [eventsData]);

  const visibleMessages = useMemo(() => {
    if (!eventsData) return [];
    if (!issuesOnly) return eventsData.messages;
    return eventsData.messages.filter((msg) => msg.events.some(isIssueEvent));
  }, [eventsData, issuesOnly]);

  // Browse mode: show admin conversation browser
  if (browser === 'feedback') {
    return (
      <AdminFeedbackBrowser
        onSelect={openObservabilityMessage}
        onClose={() => setObservabilityBrowser('none')}
      />
    );
  }

  if (browser === 'conversations') {
    return (
      <AdminConversationBrowser
        onSelect={(id) => setObservabilitySelectedConvId(id)}
        onClose={() => setObservabilityBrowser('none')}
      />
    );
  }

  // No conversation selected: show placeholder
  if (!selectedConvId) {
    return (
      <div className="flex-1 flex items-center justify-center bg-chat dark:bg-chat-dark">
        <div className="text-center">
          <div className="text-text-secondary dark:text-text-secondary-dark text-3xl font-semibold">
            从侧栏选择一个对话查看事件时间线
          </div>
          <div className="text-text-tertiary dark:text-text-tertiary-dark mt-1">
            或使用「搜索对话」查找更多
          </div>
        </div>
      </div>
    );
  }

  // Aggregate stats (events view)
  const stats = eventsData != null ? aggregateStats(eventsData.messages) : null;
  const headerTitle = eventsData?.title || selectedConvId;

  // activePathIds 在 early-return 之前已算好（见上）。有分支 = 活动路径未覆盖全部消息。
  const hasBranches = eventsData != null && activePathIds.size > 0
    && activePathIds.size < eventsData.messages.length;

  // Timeline + Detail
  return (
    <div className="flex-1 flex min-h-0 bg-chat dark:bg-chat-dark">
      {/* Main column */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header (title + conv id + tabs) */}
        <div className="px-4 pt-3 pb-2 border-b border-border dark:border-border-dark">
          <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark truncate">
            {headerTitle}
          </div>
          <ConvMetaBlock data={eventsData} fallbackConvId={selectedConvId} />
          <SegmentedTabs
            ariaLabel="Conversation detail view"
            className="mt-2"
            value={viewMode}
            options={[
              { value: 'events', label: 'Events' },
              { value: 'artifacts', label: 'Artifacts' },
            ]}
            onChange={setViewMode}
          />
        </div>

        {viewMode === 'events' ? (
          eventsLoading ? (
            <div className="flex-1 flex items-center justify-center text-text-tertiary dark:text-text-tertiary-dark text-sm">
              加载事件中...
            </div>
          ) : eventsData != null && stats != null ? (
            <>
              {/* Stats cards */}
              <div className="px-4 py-2 border-b border-border dark:border-border-dark flex gap-3 flex-wrap">
                <StatCard label="Messages" value={String(eventsData.messages.length)} />
                <StatCard label="Events" value={String(eventsData.messages.reduce((n, m) => n + m.events.length, 0))} />
                <StatCard label="Tokens In" value={formatNumber(stats.inputTokens)} />
                {stats.cacheReportedCalls > 0 ? (
                  <StatCard
                    label="Cached In ↻"
                    value={`${stats.cacheReportedCalls < stats.llmCalls ? '≥' : ''}${formatNumber(stats.cachedInputTokens)}`}
                  />
                ) : null}
                <StatCard label="Tokens Out" value={formatNumber(stats.outputTokens)} />
                <StatCard label="LLM Calls" value={String(stats.llmCalls)} />
                <StatCard
                  label="Tool Calls"
                  value={stats.toolFails > 0 ? `${stats.toolCalls} (${stats.toolFails} fail)` : String(stats.toolCalls)}
                  tone={stats.toolFails > 0 ? 'warning' : 'neutral'}
                />
                {stats.terminalErrors > 0 ? (
                  <StatCard label="Errors" value={String(stats.terminalErrors)} tone="error" />
                ) : null}
                {stats.timedOut > 0 ? (
                  <StatCard label="Timeouts" value={String(stats.timedOut)} tone="warning" />
                ) : null}
                {stats.cancelled > 0 ? (
                  <StatCard label="Cancelled" value={String(stats.cancelled)} tone="warning" />
                ) : null}
                <StatCard label="Total Time" value={formatDuration(stats.totalDurationMs)} />
              </div>
              <IssueSummaryBar
                stats={stats}
                issuesOnly={issuesOnly}
                onToggle={() => setIssuesOnly((v) => !v)}
              />

              {/* Messages & events */}
              <div className="flex-1 overflow-y-auto px-4 py-2">
                {visibleMessages.length === 0 ? (
                  <div className="py-10 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
                    当前对话没有异常事件
                  </div>
                ) : visibleMessages.map((msg) => (
                  <MessageGroupView
                    key={msg.message_id}
                    group={msg}
                    focused={msg.message_id === focusMessageId}
                    collapsed={collapsedMessages.has(msg.message_id)}
                    onToggle={() => toggleMessageCollapse(msg.message_id)}
                    offActiveBranch={isAdminMessageOffActiveBranch(
                      msg.message_id,
                      activeMessageId,
                      hasBranches,
                      activePathIds,
                    )}
                    issuesOnly={issuesOnly}
                    selectedEventId={selectedEvent?.id ?? null}
                    onSelectEvent={(e, msgId) => { setSelectedEvent(e); setSelectedMsgId(msgId); }}
                  />
                ))}
              </div>
            </>
          ) : null
        ) : (
          <ArtifactsTab convId={selectedConvId} refreshTick={refreshTick} />
        )}
      </div>

      {/* Right detail panel — only for events tab */}
      {viewMode === 'events' && selectedEvent != null ? (
        <DetailPanel
          key={selectedEvent.id}
          event={selectedEvent}
          convId={selectedConvId}
          messageId={selectedMsgId}
          onClose={() => { setSelectedEvent(null); setSelectedMsgId(null); }}
        />
      ) : null}
    </div>
  );
}

export function serializeEventToText(event: AdminEventItem): string {
  const lines: string[] = [];
  const d = event.data;
  lines.push(`ID: ${event.id}`);
  lines.push(`类型: ${event.event_type}`);
  lines.push(`Agent: ${event.agent_name || '-'}`);
  lines.push(`时间: ${parseUtcIso(event.created_at).toLocaleString('zh-CN')}`);

  if (d != null && event.event_type === 'llm_complete') {
    lines.push(`模型: ${(d.model as string) || '-'}`);
    lines.push(`耗时: ${d.duration_ms as number}ms`);
    if (d.token_usage != null) {
      const t = d.token_usage as Record<string, number>;
      lines.push(`Tokens: ${formatLlmTokenUsage(t)}`);
    }
    if (d.reasoning_content != null) lines.push(`\n--- Reasoning ---\n${d.reasoning_content as string}`);
    if (d.content != null) lines.push(`\n--- Response ---\n${d.content as string}`);
    const calls = nativeToolCalls(d);
    if (calls.length > 0) lines.push(`\n--- Tool Calls ---\n${formatNativeToolCalls(calls)}`);
  }
  if (d != null && (event.event_type === 'tool_start' || event.event_type === 'tool_complete')) {
    if (d.call_id != null) lines.push(`Call ID: ${d.call_id as string}`);
    lines.push(`工具: ${(d.tool as string) || '-'}`);
    if (d.reason != null) lines.push(`调用说明: ${d.reason as string}`);
    if (d.duration_ms != null) lines.push(`耗时: ${d.duration_ms}ms`);
    if (d.success != null) lines.push(`状态: ${d.success ? 'OK' : 'FAIL'}`);
    if (d.params != null) lines.push(`\n--- Params ---\n${JSON.stringify(d.params, null, 2)}`);
    if (d.result_data != null) lines.push(`\n--- Result ---\n${typeof d.result_data === 'string' ? d.result_data : JSON.stringify(d.result_data, null, 2)}`);
    if (d.error != null) lines.push(`\n--- Error ---\n${d.error as string}`);
    if (d.metadata != null) lines.push(`\n--- Metadata ---\n${JSON.stringify(d.metadata, null, 2)}`);
  }
  if (d != null && event.event_type === 'agent_start' && d.system_prompt != null) {
    lines.push(`\n--- System Prompt ---\n${d.system_prompt as string}`);
  }
  if (d != null && event.event_type === 'agent_start' && d.reminder != null) {
    lines.push(`\n--- Reminder ---\n${d.reminder as string}`);
  }
  if (d != null && event.event_type === 'agent_start') {
    if (d.model != null) lines.push(`模型: ${d.model as string}`);
  }
  if (d != null && event.event_type === 'error') {
    lines.push(`\n--- Error ---\n${(d.error as string) || JSON.stringify(d, null, 2)}`);
  }
  if (d != null && !['llm_complete', 'tool_start', 'tool_complete', 'agent_start', 'error'].includes(event.event_type)) {
    lines.push(`\n--- Data ---\n${JSON.stringify(d, null, 2)}`);
  }
  return lines.join('\n');
}

function DetailPanel({
  event,
  convId,
  messageId,
  onClose,
}: {
  event: AdminEventItem;
  convId: string | null;
  messageId: string | null;
  onClose: () => void;
}) {
  const { copied, copy } = useCopyFeedback();

  const handleCopy = useCallback(() => {
    copy(serializeEventToText(event));
  }, [event, copy]);

  return (
    <div className="w-[360px] flex-shrink-0 flex flex-col overflow-hidden border-l border-border dark:border-border-dark">
      <div className="px-4 pt-3 pb-2 border-b border-border dark:border-border-dark flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">
          {event.event_type}
        </h3>
        <div className="flex items-center gap-1">
          <button
            onClick={handleCopy}
            className="p-1 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
            title="复制全部内容"
          >
            {copied ? (
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3.5 8.5l3 3 6-7" />
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="5" y="5" width="9" height="9" rx="1" />
                <path d="M11 5V3a1 1 0 00-1-1H3a1 1 0 00-1 1v7a1 1 0 001 1h2" />
              </svg>
            )}
          </button>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3">
        <EventDetail event={event} convId={convId} messageId={messageId} />
      </div>
    </div>
  );
}

// ── Admin Conversation Browser (search mode in center panel) ──
function AdminConversationBrowser({
  onSelect,
  onClose,
}: {
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const [conversations, setConversations] = useState<AdminConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Refs let the refreshTick effect refresh the current page without
  // re-firing every time the user navigates.
  const queryRef = useRef(query);
  const pageRef = useRef(page);
  const pageSizeRef = useRef(pageSize);
  const refreshTick = useUIStore((s) => s.observabilityRefreshTick);
  const claim = useLatestOnly();

  const fetchConversations = useCallback(async (q: string, pageNum: number, size: number) => {
    // Latest-only drops slow older fetches (debounced search, stale page
    // changes, refreshTick bumps) so they can't overwrite a newer result set.
    const isLatest = claim();
    setLoading(true);
    try {
      const trimmed = q.trim() || undefined;
      const offset = (pageNum - 1) * size;
      const res = await api.listAdminConversations(size, offset, trimmed);
      if (!isLatest()) return;
      // refreshTick bumps may have shrunk total below our page (admin view
      // sees deletes from any user). Drop to the new last page and re-fetch;
      // recursive claim() supersedes ours so finally skips setLoading(false)
      // and the cascade renders as one continuous loading state.
      const lastPage = Math.max(1, Math.ceil(res.total / size));
      if (pageNum > lastPage) {
        pageRef.current = lastPage;
        setPage(lastPage);
        void fetchConversations(q, lastPage, size);
        return;
      }
      setConversations(res.conversations);
      setTotal(res.total);
    } catch (err) {
      if (!isLatest()) return;
      console.error('Failed to load admin conversations:', err);
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    fetchConversations(queryRef.current, pageRef.current, pageSizeRef.current);
  }, [fetchConversations, refreshTick]);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    queryRef.current = value;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      pageRef.current = 1;
      fetchConversations(value, 1, pageSizeRef.current);
    }, 300);
  }, [fetchConversations]);

  const handlePageChange = useCallback((p: number) => {
    setPage(p);
    pageRef.current = p;
    fetchConversations(queryRef.current, p, pageSizeRef.current);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchConversations]);

  const handlePageSizeChange = useCallback((size: number) => {
    setPageSize(size);
    pageSizeRef.current = size;
    setPage(1);
    pageRef.current = 1;
    fetchConversations(queryRef.current, 1, size);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchConversations]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={handleQueryChange}
        placeholder="搜索对话标题或 ID…"
        countLabel={`${total} 对话`}
        onClose={onClose}
      />

      {/* List */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={`group relative cursor-pointer transition-colors rounded-lg mb-1 px-4 py-3 ${MENU_ROW_HOVER}`}
              onClick={() => onSelect(conv.id)}
            >
              <div className="flex items-center gap-2">
                {conv.is_active && (
                  <span className="inline-block w-2 h-2 rounded-full bg-status-running flex-shrink-0" title="运行中" />
                )}
                <span className="font-medium text-text-primary dark:text-text-primary-dark truncate">
                  {conv.title || 'Untitled'}
                </span>
              </div>
              <div className="flex items-center gap-2 mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                <span>{conv.user_display_name || conv.user_id || '-'}</span>
                <span>{conv.message_count} messages</span>
                <span>{parseUtcIso(conv.updated_at).toLocaleDateString()}</span>
              </div>
            </div>
          ))}

          {loading && conversations.length === 0 && (
            <div className="py-4 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
              Loading...
            </div>
          )}

          {!loading && conversations.length === 0 && (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              {query ? '没有找到匹配的对话' : '暂无对话'}
            </div>
          )}
        </div>
      </div>

      {total > 0 && (
        <div className="px-4 pt-2 pb-4">
          <div className="max-w-3xl mx-auto">
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={handlePageChange}
              onPageSizeChange={handlePageSizeChange}
              disabled={loading}
            />
          </div>
        </div>
      )}
    </div>
  );
}

type FeedbackFilter = 'all' | 'positive' | 'negative';

function AdminFeedbackBrowser({
  onSelect,
  onClose,
}: {
  onSelect: (conversationId: string, messageId: string) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<AdminFeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FeedbackFilter>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const queryRef = useRef(query);
  const filterRef = useRef(filter);
  const pageRef = useRef(page);
  const pageSizeRef = useRef(pageSize);
  const refreshTick = useUIStore((s) => s.observabilityRefreshTick);
  const claim = useLatestOnly();

  const fetchFeedback = useCallback(async (
    q: string,
    selectedFilter: FeedbackFilter,
    pageNum: number,
    size: number,
  ) => {
    const isLatest = claim();
    setLoading(true);
    try {
      const res = await api.listAdminFeedback(
        size,
        (pageNum - 1) * size,
        q.trim() || undefined,
        selectedFilter === 'all' ? undefined : selectedFilter,
      );
      if (!isLatest()) return;
      const lastPage = Math.max(1, Math.ceil(res.total / size));
      if (pageNum > lastPage) {
        pageRef.current = lastPage;
        setPage(lastPage);
        void fetchFeedback(q, selectedFilter, lastPage, size);
        return;
      }
      setItems(res.feedback);
      setTotal(res.total);
    } catch (error) {
      if (isLatest()) console.error('Failed to load admin feedback:', error);
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    fetchFeedback(
      queryRef.current,
      filterRef.current,
      pageRef.current,
      pageSizeRef.current,
    );
  }, [fetchFeedback, refreshTick]);

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    queryRef.current = value;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      pageRef.current = 1;
      setPage(1);
      fetchFeedback(value, filterRef.current, 1, pageSizeRef.current);
    }, 300);
  }, [fetchFeedback]);

  const handleFilterChange = useCallback((value: FeedbackFilter) => {
    setFilter(value);
    filterRef.current = value;
    pageRef.current = 1;
    setPage(1);
    fetchFeedback(queryRef.current, value, 1, pageSizeRef.current);
  }, [fetchFeedback]);

  const handlePageChange = useCallback((value: number) => {
    setPage(value);
    pageRef.current = value;
    fetchFeedback(queryRef.current, filterRef.current, value, pageSizeRef.current);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchFeedback]);

  const handlePageSizeChange = useCallback((value: number) => {
    setPageSize(value);
    pageSizeRef.current = value;
    pageRef.current = 1;
    setPage(1);
    fetchFeedback(queryRef.current, filterRef.current, 1, value);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchFeedback]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={handleQueryChange}
        placeholder="搜索对话标题、对话 ID 或消息 ID…"
        countLabel={`${total} 条反馈`}
        onClose={onClose}
      />
      <div className="px-4 pb-3">
        <div className="max-w-3xl mx-auto">
          <SegmentedTabs
            value={filter}
            ariaLabel="反馈类型筛选"
            options={[
              { value: 'all', label: '全部' },
              {
                value: 'positive',
                label: (
                  <span className="inline-flex items-center gap-1 text-status-success">
                    <FeedbackRatingIcon rating="positive" size={13} />赞
                  </span>
                ),
              },
              {
                value: 'negative',
                label: (
                  <span className="inline-flex items-center gap-1 text-status-error">
                    <FeedbackRatingIcon rating="negative" size={13} />踩
                  </span>
                ),
              },
            ]}
            onChange={handleFilterChange}
          />
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {items.map((item) => (
            <button
              key={item.message_id}
              type="button"
              onClick={() => onSelect(item.conversation_id, item.message_id)}
              className={`w-full text-left rounded-lg mb-1 px-4 py-3 transition-colors ${MENU_ROW_HOVER}`}
            >
              <div className="flex items-center gap-2">
                <PillBadge
                  tone={item.feedback.rating === 'positive' ? 'success' : 'error'}
                  size="regular"
                  className="gap-1"
                >
                  <FeedbackRatingIcon rating={item.feedback.rating} size={14} />
                  {item.feedback.rating === 'positive' ? '赞' : '踩'}
                </PillBadge>
                <span className="font-medium text-text-primary dark:text-text-primary-dark truncate">
                  {item.conversation_title || 'Untitled'}
                </span>
                <span className="ml-auto shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  {formatDateTime(item.feedback.updated_at)}
                </span>
              </div>
              <div className="mt-1 truncate text-sm text-text-secondary dark:text-text-secondary-dark">
                {formatAdminInputPreview(item.user_input)}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                <span>{item.user_display_name || item.user_id || '-'}</span>
                <span className="font-mono" title={item.message_id}>{item.message_id}</span>
                {item.feedback.tags.map((tag) => (
                  <PillBadge key={tag} tone="neutral">{FEEDBACK_TAG_LABELS[tag]}</PillBadge>
                ))}
              </div>
              {item.feedback.detail ? (
                <div className="mt-1 truncate text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  {item.feedback.detail}
                </div>
              ) : null}
            </button>
          ))}

          {loading && items.length === 0 ? (
            <div className="py-4 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">Loading...</div>
          ) : null}
          {!loading && items.length === 0 ? (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              {query ? '没有找到匹配的反馈' : '暂无反馈'}
            </div>
          ) : null}
        </div>
      </div>

      {total > 0 ? (
        <div className="px-4 pt-2 pb-4">
          <div className="max-w-3xl mx-auto">
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={handlePageChange}
              onPageSizeChange={handlePageSizeChange}
              disabled={loading}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ── Message Group ──
function groupIssueCounts(group: AdminMessageGroup) {
  return group.events.reduce((acc, event) => {
    if (event.event_type === 'error') acc.errors += 1;
    else if (event.event_type === 'timed_out') acc.timeouts += 1;
    else if (event.event_type === 'cancelled') acc.cancelled += 1;
    else if (isToolFailure(event)) acc.toolFails += 1;
    else if (isPermissionDenied(event)) acc.permissionDenied += 1;
    else if (isFailedCompaction(event)) acc.compactionFails += 1;
    return acc;
  }, {
    errors: 0,
    timeouts: 0,
    cancelled: 0,
    toolFails: 0,
    permissionDenied: 0,
    compactionFails: 0,
  });
}

function MessageGroupView({
  group,
  focused,
  collapsed,
  onToggle,
  offActiveBranch,
  issuesOnly,
  selectedEventId,
  onSelectEvent,
}: {
  group: AdminMessageGroup;
  focused: boolean;
  collapsed: boolean;
  onToggle: () => void;
  offActiveBranch: boolean;
  issuesOnly: boolean;
  selectedEventId: number | null;
  onSelectEvent: (e: AdminEventItem, messageId: string) => void;
}) {
  const inputPreview = formatAdminInputPreview(group.user_input);
  const issues = groupIssueCounts(group);
  const hasHardError = issues.errors > 0;
  const hasIssues = Object.values(issues).some((n) => n > 0);
  const visibleEvents = issuesOnly ? group.events.filter(isIssueEvent) : group.events;
  const executionMetrics = group.execution_metrics as {
    total_duration_ms?: number | null;
    cached_input_tokens_partial?: boolean;
    total_token_usage?: {
      total_tokens?: number | null;
      cached_input_tokens?: number | null;
    } | null;
  } | null;
  const totalDurationMs = executionMetrics?.total_duration_ms;
  const totalTokens = executionMetrics?.total_token_usage?.total_tokens;
  const cachedInputTokens = executionMetrics?.total_token_usage?.cached_input_tokens;
  // Old persisted aggregates have no coverage bit, so treat them conservatively.
  const cachedInputTokensPartial = cachedInputTokens != null
    && executionMetrics?.cached_input_tokens_partial !== false;

  return (
    <div
      id={messageAnchorId(group.message_id)}
      className={`mb-3 scroll-mt-4 rounded-lg transition-shadow ${focused ? 'ring-2 ring-accent/60 bg-accent/5' : ''}`}
    >
      {/* Message header */}
      <button
        onClick={onToggle}
        className={`w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-lg transition-colors ${
          hasHardError
            ? 'bg-status-error/5 hover:bg-status-error/10'
            : hasIssues
              ? 'bg-status-warning/5 hover:bg-status-warning/10'
              : 'hover:bg-surface dark:hover:bg-bg-dark'
        }`}
      >
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          fill="currentColor"
          className={`text-text-tertiary dark:text-text-tertiary-dark transition-transform flex-shrink-0 ${collapsed ? '' : 'rotate-90'}`}
        >
          <path d="M3 1l5 4-5 4z" />
        </svg>
        <span className="min-w-0 text-xs font-medium text-text-primary dark:text-text-primary-dark truncate">
          {inputPreview}
        </span>
        {offActiveBranch ? (
          <span
            className="flex-shrink-0 px-1 py-px rounded bg-status-warning/10 text-status-warning text-[10px]"
            title="不在当前活动分支路径上（旁支历史消息）"
          >
            旁支
          </span>
        ) : null}
        {issues.errors > 0 ? <PillBadge tone="error">ERROR</PillBadge> : null}
        {issues.timeouts > 0 ? <PillBadge tone="warning">TIMEOUT</PillBadge> : null}
        {issues.cancelled > 0 ? <PillBadge tone="warning">CANCELLED</PillBadge> : null}
        {issues.toolFails > 0 ? <PillBadge tone="warning">{issues.toolFails} tool fail</PillBadge> : null}
        {issues.permissionDenied > 0 ? <PillBadge tone="warning">{issues.permissionDenied} denied</PillBadge> : null}
        {issues.compactionFails > 0 ? <PillBadge tone="warning">compaction fail</PillBadge> : null}
        <span className="ml-auto flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          {totalTokens != null && totalTokens > 0 ? (
            <span
              className="font-mono"
              title={cachedInputTokens != null
                ? cachedInputTokensPartial
                  ? '↻ cached input tokens (partial reporting; actual total may be higher)'
                  : '↻ cached input tokens'
                : undefined}
            >
              {formatTokens(totalTokens)} tokens
              {cachedInputTokens != null ? ` (${formatCachedTokens(cachedInputTokens, cachedInputTokensPartial)})` : ''}
              {' · '}
            </span>
          ) : null}
          {issuesOnly ? `${visibleEvents.length}/${group.events.length} events` : `${group.events.length} events`}
          {totalDurationMs != null && totalDurationMs > 0 ? (
            <span className="ml-2 font-mono">· {formatDuration(totalDurationMs)}</span>
          ) : null}
        </span>
      </button>

      <div className="ml-6 mt-1 flex flex-wrap items-center gap-2 text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
        <MetaItem label="Message"><CopyableValue value={group.message_id} mono /></MetaItem>
        {group.feedback ? (
          <>
            <PillBadge
              tone={group.feedback.rating === 'positive' ? 'success' : 'error'}
              className="gap-1"
            >
              <FeedbackRatingIcon rating={group.feedback.rating} size={12} />
              {group.feedback.rating === 'positive' ? '赞' : '踩'}
            </PillBadge>
            {group.feedback.tags.map((tag) => (
              <PillBadge key={tag} tone="neutral">{FEEDBACK_TAG_LABELS[tag]}</PillBadge>
            ))}
          </>
        ) : null}
      </div>
      {group.feedback?.detail ? (
        <div className="ml-6 mt-1 rounded-md bg-panel-accent dark:bg-bg-dark px-2 py-1.5 text-xs text-text-secondary dark:text-text-secondary-dark whitespace-pre-wrap">
          {group.feedback.detail}
        </div>
      ) : null}

      {group.uploaded_files && group.uploaded_files.length > 0 ? (
        <div className="ml-6 mt-1 flex flex-wrap gap-1.5">
          {group.uploaded_files.map((file, index) => (
            <span
              key={`${file.id ?? file.filename}-${index}`}
              className="inline-flex min-w-0 max-w-[16rem] items-center gap-1 rounded-lg bg-panel-accent px-2 py-1 text-xs text-text-secondary dark:bg-surface-dark dark:text-text-secondary-dark"
              title={file.filename}
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
              </svg>
              <span className="min-w-0 truncate">{file.filename}</span>
            </span>
          ))}
        </div>
      ) : null}

      {/* Events */}
      {!collapsed && (
        <div className="ml-4 mt-1 space-y-0.5">
          {visibleEvents.map((event) => {
            const tone = eventIssueTone(event);
            return (
              <button
                key={event.id}
                onClick={() => onSelectEvent(event, group.message_id)}
                className={`w-full text-left flex items-center gap-2 px-2 py-1 rounded text-xs transition-colors ${
                  selectedEventId === event.id
                    ? 'bg-accent/10'
                    : tone === 'error'
                      ? 'bg-status-error/5 hover:bg-status-error/10'
                      : tone === 'warning'
                        ? 'bg-status-warning/5 hover:bg-status-warning/10'
                        : 'hover:bg-surface dark:hover:bg-bg-dark'
                }`}
              >
                <span className="flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark w-[52px]">
                  {formatTime(event.created_at)}
                </span>
                {event.agent_name != null ? (
                  <PillBadge tone="accent">{event.agent_name.replace('_agent', '')}</PillBadge>
                ) : null}
                {event.id < 0 ? <PillBadge tone="accent">LIVE</PillBadge> : null}
                {tone != null ? (
                  <PillBadge tone={tone}>{tone === 'error' ? 'ERROR' : 'FAIL'}</PillBadge>
                ) : null}
                <span className={`flex-shrink-0 font-mono ${eventColor(event)}`}>
                  {event.event_type}
                </span>
                <span className={`${tone === 'error' ? 'text-status-error' : tone === 'warning' ? 'text-status-warning' : 'text-text-tertiary dark:text-text-tertiary-dark'} truncate`}>
                  {eventSummary(event)}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Event Detail ──
function EventDetail({
  event,
  convId,
  messageId,
}: {
  event: AdminEventItem;
  convId: string | null;
  messageId: string | null;
}) {
  const d = event.data;

  return (
    <div className="space-y-3 text-sm">
      {/* Meta */}
      <div className="space-y-1">
        <DetailRow label="ID" value={String(event.id)} />
        <DetailRow label="类型" value={event.event_type} />
        <DetailRow label="Agent" value={event.agent_name || '-'} />
        <DetailRow label="时间" value={parseUtcIso(event.created_at).toLocaleString('zh-CN')} />
      </div>

      {/* Type-specific details */}
      {d != null && event.event_type === 'llm_complete' ? (
        <div className="space-y-2">
          <DetailRow label="模型" value={(d.model as string) || '-'} />
          <DetailRow label="耗时" value={`${d.duration_ms as number}ms`} />
          {d.token_usage != null ? (
            <DetailRow
              label="Tokens"
              value={formatLlmTokenUsage(d.token_usage as Record<string, number>)}
            />
          ) : null}
          {d.reasoning_content != null ? (
            <DetailBlock label="Reasoning" content={d.reasoning_content as string} />
          ) : null}
          {d.content != null ? (
            <DetailBlock label="Response" content={d.content as string} />
          ) : null}
          {nativeToolCalls(d).length > 0 ? (
            <DetailBlock label="Tool Calls" content={formatNativeToolCalls(nativeToolCalls(d))} />
          ) : null}
        </div>
      ) : null}

      {d != null && (event.event_type === 'tool_start' || event.event_type === 'tool_complete') ? (
        <div className="space-y-2">
          {d.call_id != null ? <DetailRow label="Call ID" value={d.call_id as string} /> : null}
          <DetailRow label="工具" value={(d.tool as string) || '-'} />
          {d.reason != null ? <DetailBlock label="调用说明" content={d.reason as string} /> : null}
          {d.duration_ms != null ? <DetailRow label="耗时" value={`${d.duration_ms}ms`} /> : null}
          {d.success != null ? <DetailRow label="状态" value={d.success ? 'OK' : 'FAIL'} /> : null}
          {d.params != null ? (
            <DetailBlock label="Params" content={JSON.stringify(d.params, null, 2)} />
          ) : null}
          {d.result_data != null ? (
            <DetailBlock label="Result" content={typeof d.result_data === 'string' ? d.result_data : JSON.stringify(d.result_data, null, 2)} />
          ) : null}
          {d.error != null ? (
            <DetailBlock label="Error" content={d.error as string} />
          ) : null}
          {d.metadata != null ? (
            <DetailBlock label="Metadata" content={JSON.stringify(d.metadata, null, 2)} />
          ) : null}
        </div>
      ) : null}

      {event.event_type === 'agent_start' ? (
        <>
          {d?.model != null ? <DetailRow label="Model" value={d.model as string} /> : null}
          {d?.system_prompt != null ? (
            <DetailBlock label="System Prompt" content={d.system_prompt as string} />
          ) : null}
          {d?.reminder != null ? (
            <DetailBlock label="Reminder（动态，并入末条消息）" content={d.reminder as string} />
          ) : null}
          <PromptReconstructSection convId={convId} messageId={messageId} event={event} />
        </>
      ) : null}

      {d != null && event.event_type === 'error' ? (
        <DetailBlock label="Error" content={(d.error as string) || JSON.stringify(d, null, 2)} />
      ) : null}

      {/* Raw JSON fallback for other types */}
      {d != null && !['llm_complete', 'tool_start', 'tool_complete', 'agent_start', 'error'].includes(event.event_type) ? (
        <DetailBlock label="Data" content={JSON.stringify(d, null, 2)} />
      ) : null}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <span className="flex-shrink-0 w-14 text-text-tertiary dark:text-text-tertiary-dark text-xs">{label}</span>
      <span className="text-text-primary dark:text-text-primary-dark text-xs break-all">{value}</span>
    </div>
  );
}

// ── Artifacts Tab ──
function shouldUseAdminArtifactPreview(detail: ArtifactDetail): boolean {
  if (detail.has_blob) return true;
  return (
    detail.content_type === 'text/markdown' ||
    detail.content_type === 'text/html' ||
    isCsvMime(detail.content_type)
  );
}

function ArtifactsTab({ convId, refreshTick }: { convId: string; refreshTick: number }) {
  const [list, setList] = useState<ArtifactSummary[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);
  const [versionContent, setVersionContent] = useState<VersionDetail | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);
  const [downloadLoading, setDownloadLoading] = useState(false);

  // Load artifact list when conv changes
  useEffect(() => {
    setList(null);
    setSelectedId(null);
    setDetail(null);
    setViewingVersion(null);
    setVersionContent(null);
    let cancelled = false;
    setListLoading(true);
    api.listAdminConversationArtifacts(convId).then((res) => {
      if (!cancelled) setList(res.artifacts);
    }).catch((err) => {
      if (!cancelled) {
        console.error('Failed to load artifacts:', err);
        setList([]);
      }
    }).finally(() => {
      if (!cancelled) setListLoading(false);
    });
    return () => { cancelled = true; };
  }, [convId, refreshTick]);

  // Load artifact detail when selection changes
  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      setViewingVersion(null);
      setVersionContent(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    setViewingVersion(null);
    setVersionContent(null);
    api.getAdminConversationArtifact(convId, selectedId).then((res) => {
      if (!cancelled) {
        setDetail(res);
        setViewingVersion(res.current_version);
      }
    }).catch((err) => {
      if (!cancelled) {
        console.error('Failed to load artifact:', err);
        setDetail(null);
      }
    }).finally(() => {
      if (!cancelled) setDetailLoading(false);
    });
    return () => { cancelled = true; };
  }, [convId, selectedId]);

  // Load specific version content when viewing a non-current version
  useEffect(() => {
    // Early-return branches must also clear versionLoading: the in-flight
    // fetch's `.finally` is gated on `!cancelled`, so switching away from a
    // loading version (e.g. v3 → current) leaves the spinner hanging.
    if (selectedId == null || detail == null || viewingVersion == null) {
      setVersionContent(null);
      setVersionLoading(false);
      return;
    }
    if (viewingVersion === detail.current_version) {
      setVersionContent(null);
      setVersionLoading(false);
      return;
    }
    let cancelled = false;
    // Clear stale content before fetching so the viewer shows a loading
    // state instead of the previously-displayed version's content.
    setVersionContent(null);
    setVersionLoading(true);
    api.getAdminConversationArtifactVersion(convId, selectedId, viewingVersion).then((res) => {
      if (!cancelled) setVersionContent(res);
    }).catch((err) => {
      if (!cancelled) {
        console.error('Failed to load version:', err);
        setVersionContent(null);
      }
    }).finally(() => {
      if (!cancelled) setVersionLoading(false);
    });
    return () => { cancelled = true; };
  }, [convId, selectedId, detail, viewingVersion]);

  // Showing a non-current version: require the loaded content to match the
  // selected version, otherwise show a loading state (defends against the
  // gap between selecting a version and the fetch resolving).
  const isViewingCurrent =
    detail != null && viewingVersion != null && viewingVersion === detail.current_version;
  const versionContentMatches =
    versionContent != null && versionContent.version === viewingVersion;
  const versionContentReady = isViewingCurrent || versionContentMatches;
  const displayedContent = isViewingCurrent
    ? detail?.content ?? ''
    : versionContentMatches
      ? versionContent!.content
      : '';
  const showingRichPreview =
    detail != null && versionContentReady && shouldUseAdminArtifactPreview(detail);
  const contentClassName = showingRichPreview
    ? detail?.content_type === 'text/markdown'
      ? 'flex-1 min-h-0 overflow-y-auto'
      : 'flex-1 min-h-0 overflow-hidden'
    : 'flex-1 min-h-0 overflow-y-auto px-4 py-3';

  const handleArtifactDownload = useCallback(async () => {
    if (detail == null || !versionContentReady) return;

    setDownloadLoading(true);
    try {
      if (detail.has_blob) {
        const url = await api.fetchAdminArtifactRawObjectUrl(convId, detail.id);
        triggerObjectUrlDownload(detail.original_filename ?? detail.title, url);
        return;
      }

      const filename = getTextArtifactDownloadFilename(detail.title, detail.content_type);
      triggerBlobDownload(
        filename,
        new Blob([displayedContent], { type: `${detail.content_type};charset=utf-8` })
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : '下载失败，请稍后重试';
      window.alert(message);
    } finally {
      setDownloadLoading(false);
    }
  }, [convId, detail, displayedContent, versionContentReady]);

  return (
    <div className="flex-1 flex min-h-0">
      {/* List */}
      <div className="w-[280px] flex-shrink-0 border-r border-border dark:border-border-dark overflow-y-auto">
        {listLoading ? (
          <div className="p-4 text-xs text-text-tertiary dark:text-text-tertiary-dark">加载中…</div>
        ) : list == null || list.length === 0 ? (
          <div className="p-4 text-xs text-text-tertiary dark:text-text-tertiary-dark">该会话暂无 artifacts</div>
        ) : (
          <div className="py-1">
            {list.map((art) => (
              <button
                key={art.id}
                onClick={() => setSelectedId(art.id)}
                className={`w-full text-left px-3 py-2 transition-colors ${
                  selectedId === art.id
                    ? 'bg-accent/10'
                    : 'hover:bg-surface dark:hover:bg-bg-dark'
                }`}
              >
                <div className="text-xs font-medium text-text-primary dark:text-text-primary-dark truncate">
                  {art.title}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-text-tertiary dark:text-text-tertiary-dark">
                  <span className="font-mono">{art.content_type}</span>
                  <span>v{art.current_version}</span>
                  {art.source ? <span>· {art.source}</span> : null}
                </div>
                <div className="mt-0.5 text-[10px] text-text-tertiary dark:text-text-tertiary-dark truncate">
                  {parseUtcIso(art.updated_at).toLocaleString('zh-CN')}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Viewer */}
      <div className="flex-1 flex flex-col min-w-0">
        {selectedId == null ? (
          <div className="flex-1 flex items-center justify-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            从左侧选择一个 artifact 查看内容
          </div>
        ) : detailLoading ? (
          <div className="flex-1 flex items-center justify-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            加载中…
          </div>
        ) : detail == null ? (
          <div className="flex-1 flex items-center justify-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            加载失败
          </div>
        ) : (
          <>
            {/* Artifact header */}
            <div className="px-4 pt-3 pb-2 border-b border-border dark:border-border-dark">
              <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark truncate">
                {detail.title}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-text-tertiary dark:text-text-tertiary-dark flex-wrap">
                <span className="font-mono">{detail.content_type}</span>
                <span>·</span>
                <span>ID: {detail.id}</span>
                {detail.source ? <><span>·</span><span>{detail.source}</span></> : null}
                {detail.original_filename ? <><span>·</span><span>{detail.original_filename}</span></> : null}
                {detail.versions.length > 0 ? (
                  <>
                    <span>·</span>
                    <span className="relative">
                      <select
                        value={viewingVersion ?? detail.current_version}
                        onChange={(e) => setViewingVersion(Number(e.target.value))}
                        className={SELECT_COMPACT}
                      >
                        {detail.versions.map((v) => (
                          <option key={v.version} value={v.version}>
                            v{v.version} ({v.update_type}){v.version === detail.current_version ? ' · current' : ''}
                          </option>
                        ))}
                      </select>
                      {SELECT_CHEVRON_COMPACT}
                    </span>
                    {versionLoading ? <span>加载…</span> : null}
                  </>
                ) : null}
                <button
                  type="button"
                  onClick={handleArtifactDownload}
                  disabled={downloadLoading || !versionContentReady}
                  className={`${BUTTON_SECONDARY} inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px]`}
                  aria-label="下载 artifact"
                  title="下载当前查看的版本"
                >
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 14 14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    aria-hidden="true"
                  >
                    <path d="M7 2v7.5M4 7l3 3 3-3M2.5 11.5h9" />
                  </svg>
                  {downloadLoading ? '下载中…' : '下载'}
                </button>
              </div>
            </div>

            {/* Content */}
            <div className={contentClassName}>
              {versionContentReady ? (
                showingRichPreview && detail != null ? (
                  <ArtifactPreviewContent
                    sessionId={convId}
                    artifactId={detail.id}
                    content={displayedContent}
                    contentType={detail.content_type}
                    hasBlob={!!detail.has_blob}
                    originalFilename={detail.original_filename}
                    refreshKey={detail.updated_at}
                    fetchRawBlob={api.fetchAdminArtifactRawBlob}
                    fetchRawObjectUrl={api.fetchAdminArtifactRawObjectUrl}
                    pendingFlush={false}
                    useLocalPreview={false}
                    showUnsupportedBinaryDownload={false}
                  />
                ) : (
                  <pre className="text-xs text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words font-mono">
                    {displayedContent}
                  </pre>
                )
              ) : (
                <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  加载版本内容中…
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Model Messages Reconstruction (admin forensics) ──
// 重建某发 agent_start 后 LLM 调用的 OpenAI-compatible messages：messages 走
// 分支正确的历史重放，model 使用当次持久化值，不重新生成动态内容。
function PromptReconstructSection({
  convId,
  messageId,
  event,
}: {
  convId: string | null;
  messageId: string | null;
  event: AdminEventItem;
}) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<api.AdminPromptReconstructResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const eventId = event.event_id;
  const canReconstruct = convId != null && messageId != null && eventId != null;

  const handleReconstruct = useCallback(() => {
    if (convId == null || messageId == null || eventId == null) return;
    setLoading(true);
    setError(null);
    api.getAdminPromptReconstruct(convId, messageId, eventId)
      .then(setResult)
      .catch((err) => setError(err instanceof Error ? err.message : '重建失败'))
      .finally(() => setLoading(false));
  }, [convId, messageId, eventId]);

  const handleDownload = useCallback(() => {
    if (!result) return;
    const blob = new Blob([JSON.stringify({
      model: result.model,
      exposed_tool_names: result.exposed_tool_names,
      messages: result.messages,
    }, null, 2)], {
      type: 'application/json;charset=utf-8',
    });
    triggerBlobDownload(`model-messages-${messageId ?? 'msg'}-${eventId ?? 'evt'}.json`, blob);
  }, [result, messageId, eventId]);

  return (
    <div className="space-y-2 border-t border-border dark:border-border-dark pt-3">
      <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
        重建此发 OpenAI-compatible messages 和实际暴露的工具名（不包含 tools schema 或 provider chat template 后的 token 序列）
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={handleReconstruct}
          disabled={!canReconstruct || loading}
          className="px-2 py-1 rounded-md text-xs bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50 transition-colors"
        >
          {loading ? '重建中…' : '重建 Messages'}
        </button>
        {result ? (
          <button onClick={handleDownload} className="text-xs text-accent">
            下载 JSON
          </button>
        ) : null}
      </div>
      {!canReconstruct ? (
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
          该事件缺少 event_id（早于此能力上线），无法重建。
        </div>
      ) : null}
      {error ? <div className="text-xs text-status-error">{error}</div> : null}
      {result ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap text-xs text-text-tertiary dark:text-text-tertiary-dark">
            <span>{result.messages.length} 条消息 · {result.agent_name ?? '-'}</span>
            {!result.has_reminder ? (
              <span className="px-1 py-px rounded bg-status-warning/10 text-status-warning text-[10px]">
                无持久化 reminder（旧事件：仅 system + 历史）
              </span>
            ) : null}
          </div>
          <DetailRow label="Model" value={result.model ?? '-'} />
          <DetailRow
            label="Exposed tools"
            value={
              result.exposed_tool_names == null
                ? '未采集（旧事件）'
                : result.exposed_tool_names.join(', ') || '（无）'
            }
          />
          <DetailBlock label="重建 Messages" content={JSON.stringify(result.messages, null, 2)} />
        </div>
      ) : null}
    </div>
  );
}

function DetailBlock({ label, content }: { label: string; content: string }) {
  const [expanded, setExpanded] = useState(false);
  const preview = content.length > 300 && !expanded ? content.slice(0, 300) + '...' : content;

  return (
    <div>
      <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark mb-1">{label}</div>
      <pre className="text-xs text-text-primary dark:text-text-primary-dark bg-surface dark:bg-surface-dark rounded-lg p-2 overflow-x-auto whitespace-pre-wrap break-words max-h-80 overflow-y-auto">
        {preview}
      </pre>
      {content.length > 300 ? (
        <button
          onClick={() => setExpanded((prev) => !prev)}
          className="text-xs text-accent mt-1"
        >
          {expanded ? '收起' : '展开全部'}
        </button>
      ) : null}
    </div>
  );
}

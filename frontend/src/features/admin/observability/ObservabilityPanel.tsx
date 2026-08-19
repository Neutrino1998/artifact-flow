'use client';

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import type { ReactNode } from 'react';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { CopyIcon } from '@/components/ui/CopyIcon';
import { FeedbackRatingIcon } from '@/components/ui/FeedbackRatingIcon';
import { PillBadge } from '@/components/ui/PillBadge';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import * as api from '@/lib/api';
import { parseUtcIso } from '@/lib/time';
import { formatDuration } from '@/lib/formatDuration';
import { formatCachedTokens, formatTokens } from '@/lib/formatTokens';
import type {
  AdminMessageGroup,
  AdminEventItem,
  AdminConversationEventsResponse,
} from '@/lib/api';
import { FEEDBACK_TAG_LABELS } from '@/lib/messageFeedback';
import { useUIStore } from '@/stores/uiStore';
import { connectSSE } from '@/lib/sse';
import {
  ADMIN_TERMINAL_EVENTS,
  appendAdminLiveEvent,
  formatAdminInputPreview,
  isAdminMessageOffActiveBranch,
} from './adminLiveEvents';
import {
  aggregateStats,
  eventIssueTone,
  eventSummary,
  formatNumber,
  isFailedCompaction,
  isIssueEvent,
  isPermissionDenied,
  isToolFailure,
  type AggregatedStats,
} from './eventDiagnostics';
import {
  AdminConversationBrowser,
  AdminFeedbackBrowser,
} from './ObservabilityBrowsers';
import AdminArtifactInspector from './AdminArtifactInspector';
import EventDetailPanel from './EventDetailPanel';

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
  const highlightedMessageId = useUIStore((s) => s.observabilityHighlightedMessageId);
  const focusRequestId = useUIStore((s) => s.observabilityFocusRequestId);
  const focusConsumedId = useUIStore((s) => s.observabilityFocusConsumedId);
  const consumeFocusRequest = useUIStore((s) => s.consumeObservabilityFocusRequest);
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
    if (browser !== 'none') return;
    if (focusRequestId <= focusConsumedId) return;
    if (!eventsData || !highlightedMessageId) return;
    if (!eventsData.messages.some((message) => message.message_id === highlightedMessageId)) return;

    // Selecting feedback from the already-open conversation does not change
    // selectedConvId, so the conversation-reset effect above will not run.
    // Restore a view in which every message is renderable, then let this effect
    // run again after React commits that DOM before consuming the focus request.
    if (viewMode !== 'events' || issuesOnly) {
      setViewMode('events');
      setIssuesOnly(false);
      return;
    }

    const target = document.getElementById(messageAnchorId(highlightedMessageId));
    if (!target) return;
    setCollapsedMessages((prev) => {
      if (!prev.has(highlightedMessageId)) return prev;
      const next = new Set(prev);
      next.delete(highlightedMessageId);
      return next;
    });
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    consumeFocusRequest(focusRequestId);
  }, [
    browser,
    consumeFocusRequest,
    eventsData,
    focusConsumedId,
    focusRequestId,
    highlightedMessageId,
    issuesOnly,
    viewMode,
  ]);

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
          // turn handler's ERROR for this message is authoritative terminal state.
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
                    focused={msg.message_id === highlightedMessageId}
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
          <AdminArtifactInspector
            conversationId={selectedConvId}
            refreshTick={refreshTick}
          />
        )}
      </div>

      {/* Right detail panel — only for events tab */}
      {viewMode === 'events' && selectedEvent != null ? (
        <EventDetailPanel
          key={selectedEvent.id}
          event={selectedEvent}
          conversationId={selectedConvId}
          messageId={selectedMsgId}
          onClose={() => { setSelectedEvent(null); setSelectedMsgId(null); }}
        />
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
                <span className="flex-shrink-0 whitespace-nowrap tabular-nums text-text-tertiary dark:text-text-tertiary-dark">
                  {formatDateTime(event.created_at)}
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

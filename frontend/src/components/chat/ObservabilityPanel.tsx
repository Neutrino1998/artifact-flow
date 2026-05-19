'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import * as api from '@/lib/api';
import { parseUtcIso } from '@/lib/time';
import PanelSearchBar from './PanelSearchBar';
import Pagination from './Pagination';
import type {
  AdminConversationSummary,
  AdminMessageGroup,
  AdminEventItem,
  AdminConversationEventsResponse,
} from '@/lib/api';
import type { ArtifactSummary, ArtifactDetail, VersionDetail } from '@/types';
import { useUIStore } from '@/stores/uiStore';
import { useLatestOnly } from '@/hooks/useLatestOnly';

const DEFAULT_PAGE_SIZE = 20;

// ── Event type colors ──
function eventColor(type: string): string {
  if (type === 'error') return 'text-status-error';
  if (type.startsWith('permission')) return 'text-yellow-500 dark:text-yellow-400';
  if (type === 'llm_complete') return 'text-accent';
  if (type.startsWith('tool_')) return 'text-blue-500 dark:text-blue-400';
  if (type.startsWith('agent_')) return 'text-purple-500 dark:text-purple-400';
  return 'text-text-tertiary dark:text-text-tertiary-dark';
}

function eventSummary(event: AdminEventItem): string {
  const d = event.data;
  if (!d) return '';
  switch (event.event_type) {
    case 'llm_complete': {
      const tokens = d.token_usage as Record<string, number> | undefined;
      const model = (d.model as string) || '';
      const dur = d.duration_ms as number | undefined;
      return `${model} | ${tokens?.input_tokens ?? 0}/${tokens?.output_tokens ?? 0} tokens | ${dur ?? 0}ms`;
    }
    case 'tool_start':
      return `${d.tool as string}`;
    case 'tool_complete': {
      const ok = d.success as boolean;
      const dur = d.duration_ms as number | undefined;
      return `${d.tool as string} ${ok ? 'OK' : 'FAIL'} ${dur ?? 0}ms`;
    }
    case 'agent_start':
      return d.agent as string;
    case 'agent_complete':
      return `${d.agent as string} done`;
    case 'error':
      return (d.error as string)?.slice(0, 80) || 'error';
    case 'permission_request':
      return `${d.tool as string} (${d.permission_level as string})`;
    case 'permission_result':
      return d.approved ? 'approved' : 'denied';
    case 'user_input':
      return (d.content as string)?.slice(0, 60) || '';
    default:
      return '';
  }
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
  outputTokens: number;
  llmCalls: number;
  toolCalls: number;
  toolFails: number;
  totalDurationMs: number;
}

function aggregateStats(messages: AdminMessageGroup[]): AggregatedStats {
  const stats: AggregatedStats = { inputTokens: 0, outputTokens: 0, llmCalls: 0, toolCalls: 0, toolFails: 0, totalDurationMs: 0 };
  for (const msg of messages) {
    const metrics = msg.execution_metrics as Record<string, number> | null;
    if (metrics?.total_duration_ms) stats.totalDurationMs += metrics.total_duration_ms;
    for (const ev of msg.events) {
      const d = ev.data;
      if (!d) continue;
      if (ev.event_type === 'llm_complete') {
        stats.llmCalls++;
        const tokens = d.token_usage as Record<string, number> | undefined;
        if (tokens) {
          stats.inputTokens += tokens.input_tokens ?? 0;
          stats.outputTokens += tokens.output_tokens ?? 0;
        }
      } else if (ev.event_type === 'tool_complete') {
        stats.toolCalls++;
        if (!(d.success as boolean)) stats.toolFails++;
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

function formatDuration(ms: number): string {
  if (ms < 1_000) return `${ms}ms`;
  const totalSecs = Math.floor(ms / 1_000);
  if (totalSecs < 60) return `${totalSecs}s`;
  const mins = Math.floor(totalSecs / 60);
  const secs = totalSecs % 60;
  return `${mins}m ${secs}s`;
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-3 py-1.5 rounded-lg bg-panel-accent dark:bg-surface-dark">
      <div className="text-[10px] text-text-tertiary dark:text-text-tertiary-dark uppercase tracking-wide">{label}</div>
      <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">{value}</div>
    </div>
  );
}

// ── Main Panel ──
export default function ObservabilityPanel() {
  const selectedConvId = useUIStore((s) => s.observabilitySelectedConvId);
  const browseVisible = useUIStore((s) => s.observabilityBrowseVisible);
  const setObservabilityBrowseVisible = useUIStore((s) => s.setObservabilityBrowseVisible);
  const setObservabilitySelectedConvId = useUIStore((s) => s.setObservabilitySelectedConvId);

  // Timeline state
  const [eventsData, setEventsData] = useState<AdminConversationEventsResponse | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [collapsedMessages, setCollapsedMessages] = useState<Set<string>>(new Set());
  const [selectedEvent, setSelectedEvent] = useState<AdminEventItem | null>(null);
  const refreshTick = useUIStore((s) => s.observabilityRefreshTick);
  const [viewMode, setViewMode] = useState<'events' | 'artifacts'>('events');

  useEffect(() => {
    setViewMode('events');
  }, [selectedConvId]);

  // Fetch events when selected conversation changes or refresh is triggered
  useEffect(() => {
    if (!selectedConvId) {
      setEventsData(null);
      setSelectedEvent(null);
      return;
    }
    let cancelled = false;
    setEventsData(null);
    setSelectedEvent(null);
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

  const toggleMessageCollapse = useCallback((msgId: string) => {
    setCollapsedMessages((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  }, []);

  // Browse mode: show admin conversation browser
  if (browseVisible) {
    return (
      <AdminConversationBrowser
        onSelect={(id) => setObservabilitySelectedConvId(id)}
        onClose={() => setObservabilityBrowseVisible(false)}
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

  // Timeline + Detail
  return (
    <div className="flex-1 flex min-h-0 bg-chat dark:bg-chat-dark">
      {/* Main column */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header (title + tabs) */}
        <div className="px-4 pt-3 pb-2 border-b border-border dark:border-border-dark">
          <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark truncate">
            {headerTitle}
          </div>
          <div className="mt-2 inline-flex p-0.5 rounded-lg bg-panel-accent dark:bg-surface-dark text-xs">
            <TabButton active={viewMode === 'events'} onClick={() => setViewMode('events')}>
              Events
            </TabButton>
            <TabButton active={viewMode === 'artifacts'} onClick={() => setViewMode('artifacts')}>
              Artifacts
            </TabButton>
          </div>
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
                <StatCard label="Tokens Out" value={formatNumber(stats.outputTokens)} />
                <StatCard label="LLM Calls" value={String(stats.llmCalls)} />
                <StatCard label="Tool Calls" value={stats.toolFails > 0 ? `${stats.toolCalls} (${stats.toolFails} fail)` : String(stats.toolCalls)} />
                <StatCard label="Total Time" value={formatDuration(stats.totalDurationMs)} />
              </div>

              {/* Messages & events */}
              <div className="flex-1 overflow-y-auto px-4 py-2">
                {eventsData.messages.map((msg) => (
                  <MessageGroupView
                    key={msg.message_id}
                    group={msg}
                    collapsed={collapsedMessages.has(msg.message_id)}
                    onToggle={() => toggleMessageCollapse(msg.message_id)}
                    selectedEventId={selectedEvent?.id ?? null}
                    onSelectEvent={setSelectedEvent}
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
        <DetailPanel key={selectedEvent.id} event={selectedEvent} onClose={() => setSelectedEvent(null)} />
      ) : null}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 rounded-md transition-colors ${
        active
          ? 'bg-surface dark:bg-bg-dark text-accent font-medium shadow-sm'
          : 'text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark'
      }`}
    >
      {children}
    </button>
  );
}

function serializeEventToText(event: AdminEventItem): string {
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
      lines.push(`Tokens: in: ${t.input_tokens} | out: ${t.output_tokens}`);
    }
    if (d.reasoning_content != null) lines.push(`\n--- Reasoning ---\n${d.reasoning_content as string}`);
    if (d.content != null) lines.push(`\n--- Response ---\n${d.content as string}`);
  }
  if (d != null && (event.event_type === 'tool_start' || event.event_type === 'tool_complete')) {
    lines.push(`工具: ${(d.tool as string) || '-'}`);
    if (d.duration_ms != null) lines.push(`耗时: ${d.duration_ms}ms`);
    if (d.success != null) lines.push(`状态: ${d.success ? 'OK' : 'FAIL'}`);
    if (d.params != null) lines.push(`\n--- Params ---\n${JSON.stringify(d.params, null, 2)}`);
    if (d.result_data != null) lines.push(`\n--- Result ---\n${typeof d.result_data === 'string' ? d.result_data : JSON.stringify(d.result_data, null, 2)}`);
    if (d.error != null) lines.push(`\n--- Error ---\n${d.error as string}`);
  }
  if (d != null && event.event_type === 'agent_start' && d.system_prompt != null) {
    lines.push(`\n--- System Prompt ---\n${d.system_prompt as string}`);
  }
  if (d != null && event.event_type === 'error') {
    lines.push(`\n--- Error ---\n${(d.error as string) || JSON.stringify(d, null, 2)}`);
  }
  if (d != null && !['llm_complete', 'tool_start', 'tool_complete', 'agent_start', 'error'].includes(event.event_type)) {
    lines.push(`\n--- Data ---\n${JSON.stringify(d, null, 2)}`);
  }
  return lines.join('\n');
}

function DetailPanel({ event, onClose }: { event: AdminEventItem; onClose: () => void }) {
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
        <EventDetail event={event} />
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
        placeholder="搜索对话标题或 ID..."
        countLabel={`${total} 对话`}
        onClose={onClose}
      />

      {/* List */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className="group relative cursor-pointer transition-colors rounded-lg mb-1 hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60 px-4 py-3"
              onClick={() => onSelect(conv.id)}
            >
              <div className="flex items-center gap-2">
                {conv.is_active && (
                  <span className="inline-block w-2 h-2 rounded-full bg-orange-500 flex-shrink-0" title="运行中" />
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
            <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-2xl px-4">
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
        </div>
      )}
    </div>
  );
}

// ── Message Group ──
function MessageGroupView({
  group,
  collapsed,
  onToggle,
  selectedEventId,
  onSelectEvent,
}: {
  group: AdminMessageGroup;
  collapsed: boolean;
  onToggle: () => void;
  selectedEventId: number | null;
  onSelectEvent: (e: AdminEventItem) => void;
}) {
  const inputPreview = group.user_input.slice(0, 80) + (group.user_input.length > 80 ? '...' : '');

  return (
    <div className="mb-3">
      {/* Message header */}
      <button
        onClick={onToggle}
        className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-surface dark:hover:bg-bg-dark transition-colors"
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
        <span className="text-xs font-medium text-text-primary dark:text-text-primary-dark truncate">
          {inputPreview}
        </span>
        <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          {group.events.length} events
        </span>
      </button>

      {/* Events */}
      {!collapsed && (
        <div className="ml-4 mt-1 space-y-0.5">
          {group.events.map((event) => (
            <button
              key={event.id}
              onClick={() => onSelectEvent(event)}
              className={`w-full text-left flex items-center gap-2 px-2 py-1 rounded text-xs transition-colors ${
                selectedEventId === event.id
                  ? 'bg-accent/10'
                  : 'hover:bg-surface dark:hover:bg-bg-dark'
              }`}
            >
              <span className="flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark w-[52px]">
                {formatTime(event.created_at)}
              </span>
              {event.agent_name != null ? (
                <span className="flex-shrink-0 px-1 py-px rounded bg-purple-500/10 text-purple-600 dark:text-purple-400 text-[10px]">
                  {event.agent_name.replace('_agent', '')}
                </span>
              ) : null}
              <span className={`flex-shrink-0 font-mono ${eventColor(event.event_type)}`}>
                {event.event_type}
              </span>
              <span className="text-text-tertiary dark:text-text-tertiary-dark truncate">
                {eventSummary(event)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Event Detail ──
function EventDetail({ event }: { event: AdminEventItem }) {
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
              value={`in: ${(d.token_usage as Record<string, number>).input_tokens} | out: ${(d.token_usage as Record<string, number>).output_tokens}`}
            />
          ) : null}
          {d.reasoning_content != null ? (
            <DetailBlock label="Reasoning" content={d.reasoning_content as string} />
          ) : null}
          {d.content != null ? (
            <DetailBlock label="Response" content={d.content as string} />
          ) : null}
        </div>
      ) : null}

      {d != null && (event.event_type === 'tool_start' || event.event_type === 'tool_complete') ? (
        <div className="space-y-2">
          <DetailRow label="工具" value={(d.tool as string) || '-'} />
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
        </div>
      ) : null}

      {d != null && event.event_type === 'agent_start' && d.system_prompt != null ? (
        <DetailBlock label="System Prompt" content={d.system_prompt as string} />
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
function ArtifactsTab({ convId, refreshTick }: { convId: string; refreshTick: number }) {
  const [list, setList] = useState<ArtifactSummary[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);
  const [versionContent, setVersionContent] = useState<VersionDetail | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);

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

  return (
    <div className="flex-1 flex min-h-0">
      {/* List */}
      <div className="w-[280px] flex-shrink-0 border-r border-border dark:border-border-dark overflow-y-auto">
        {listLoading ? (
          <div className="p-4 text-xs text-text-tertiary dark:text-text-tertiary-dark">加载中...</div>
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
            加载中...
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
                    <select
                      value={viewingVersion ?? detail.current_version}
                      onChange={(e) => setViewingVersion(Number(e.target.value))}
                      className="text-xs bg-bg dark:bg-bg-dark border border-border dark:border-border-dark rounded px-1.5 py-0.5 text-text-secondary dark:text-text-secondary-dark"
                    >
                      {detail.versions.map((v) => (
                        <option key={v.version} value={v.version}>
                          v{v.version} ({v.update_type}){v.version === detail.current_version ? ' · current' : ''}
                        </option>
                      ))}
                    </select>
                    {versionLoading ? <span>加载...</span> : null}
                  </>
                ) : null}
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto px-4 py-3">
              {versionContentReady ? (
                <pre className="text-xs text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words font-mono">
                  {displayedContent}
                </pre>
              ) : (
                <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  加载版本内容中...
                </div>
              )}
            </div>
          </>
        )}
      </div>
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

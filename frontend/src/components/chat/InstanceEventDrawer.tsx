'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '@/lib/api';
import type {
  AdminInstanceEventsResponse,
  InstanceDiagnosticEvent,
  InstanceDiagnosticEventType,
  InstanceEventMetricSnapshot,
} from '@/types';
import type {
  InstanceEventKind,
  InstanceHeartbeat,
} from '@/lib/api';
import { parseUtcIso } from '@/lib/time';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { PillBadge } from '@/components/ui/PillBadge';

export type InstanceEventFilter = InstanceEventKind;

interface Props {
  instance: InstanceHeartbeat;
  initialFilter: InstanceEventFilter;
  onClose: () => void;
  onOpenConversation: (conversationId: string) => void;
}

const FILTERS: { id: InstanceEventFilter; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'error', label: 'ERROR' },
  { id: 'wedge', label: 'Wedge' },
  { id: 'loop_lag', label: 'Loop lag' },
];

const TYPE_LABEL: Record<InstanceDiagnosticEventType, string> = {
  error: 'ERROR',
  wedge: 'Watchdog wedge',
  loop_lag: 'Loop lag',
};

const SOURCE_LABEL: Record<InstanceDiagnosticEvent['source'], string> = {
  runtime_log: '错误日志',
  loop_lag: 'Watchdog',
};

const SOURCE_KEY_LABEL: Record<string, string> = {
  error_log: '错误日志',
  loop_lag: 'Watchdog 日志',
  metrics: '运行指标',
};

const STATUS_LABEL: Record<InstanceHeartbeat['status'], string> = {
  green: '正常',
  yellow: '告警',
  red: '失联',
};

function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  const value = parseUtcIso(ts);
  return Number.isNaN(value.getTime()) ? ts : value.toLocaleString('zh-CN');
}

function eventMatches(event: InstanceDiagnosticEvent, filter: InstanceEventFilter): boolean {
  if (filter === 'all') return true;
  return event.type === filter;
}

function metricText(metric: InstanceEventMetricSnapshot | null | undefined): string {
  if (!metric) return '—';
  const cpu = metric.process?.cpu_pct;
  const rss = metric.process?.rss_mb;
  const parts = [
    cpu != null ? `CPU ${cpu}%` : null,
    rss != null ? `RSS ${rss}M` : null,
    metric.in_flight != null ? `在途 ${metric.in_flight}` : null,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : '—';
}

export function serializeInstanceEvents(instanceId: string, events: InstanceDiagnosticEvent[]): string {
  return [
    `Instance: ${instanceId}`,
    ...events.map((event) => JSON.stringify(event, null, 2)),
  ].join('\n\n');
}

function HeartbeatSummary({ instance }: { instance: InstanceHeartbeat }) {
  const wedge = instance.last_wedge;
  const heal = instance.last_autoheal;
  const proc = instance.process ?? {};
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-border dark:border-border-dark bg-bg/60 dark:bg-bg-dark/60 p-3 text-xs">
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">当前状态</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">{STATUS_LABEL[instance.status]}</div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">本进程 ERROR</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">{instance.error_count ?? 0}</div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">最近 ERROR</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">{formatTime(instance.last_error_ts)}</div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">最近 wedge</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">
          {wedge?.ts ? `${formatTime(wedge.ts)}${wedge.lag_ms != null ? ` · ≥${Math.round(wedge.lag_ms)}ms` : ''}` : '—'}
        </div>
      </div>
      {heal?.ts && (
        <div className="col-span-2">
          <div className="text-text-tertiary dark:text-text-tertiary-dark">最近 autoheal</div>
          <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">
            {formatTime(heal.ts)} · {heal.reason ?? 'unhealthy'}
          </div>
        </div>
      )}
      <div className="col-span-2 mt-1 border-t border-border dark:border-border-dark pt-2 font-medium text-text-secondary dark:text-text-secondary-dark">
        运行详情
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">CPU</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">{proc.cpu_pct != null ? `${proc.cpu_pct}%` : '—'}</div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">FDs</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">{proc.open_fds ?? '—'}</div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">DB pool</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">
          {instance.db_pool
            ? `${instance.db_pool.in_use ?? 0}/${instance.db_pool.size ?? 0}${instance.db_pool.overflow ? ` +${instance.db_pool.overflow}` : ''}`
            : '—'}
        </div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">Redis</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">
          {instance.redis?.used_mb != null ? `${instance.redis.used_mb}M` : '—'}
        </div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">长跑任务</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">{instance.tasks_long_running ?? 0}</div>
      </div>
      <div>
        <div className="text-text-tertiary dark:text-text-tertiary-dark">data/</div>
        <div className="mt-0.5 text-text-primary dark:text-text-primary-dark">
          {instance.data_dir_mb != null ? `${instance.data_dir_mb}M` : '—'}
        </div>
      </div>
      <div className="col-span-2">
        <div className="text-text-tertiary dark:text-text-tertiary-dark">启动时间</div>
        <div className="mt-0.5 break-words text-text-primary dark:text-text-primary-dark">
          {formatTime(instance.started_at)}
        </div>
      </div>
    </div>
  );
}

function StackGroups({ event }: { event: InstanceDiagnosticEvent }) {
  const groups = [
    ...(event.threads ?? []).map((owner) => ({ ...owner, kind: owner.event_loop ? '事件循环线程' : '线程' })),
    ...(event.tasks ?? []).map((owner) => ({ ...owner, kind: 'Async task' })),
  ];
  if (groups.length === 0) return null;

  return (
    <details className="mt-3">
      <summary className="cursor-pointer select-none text-xs text-accent">查看调用栈（{groups.length}）</summary>
      <div className="mt-2 space-y-2">
        {groups.map((owner, index) => (
          <div key={`${owner.kind}-${owner.name}-${index}`} className="rounded-md bg-bg dark:bg-bg-dark p-2">
            <div className="mb-1 text-[11px] font-medium text-text-secondary dark:text-text-secondary-dark">
              {owner.kind} · {owner.name}
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-all text-[10px] leading-4 text-text-tertiary dark:text-text-tertiary-dark">
              {owner.stack.length > 0 ? owner.stack.join('\n') : '无 Python 栈'}
            </pre>
          </div>
        ))}
      </div>
    </details>
  );
}

function EventCard({
  event,
  onOpenConversation,
}: {
  event: InstanceDiagnosticEvent;
  onOpenConversation: (conversationId: string) => void;
}) {
  const isWedge = event.type === 'wedge';
  return (
    <article className="rounded-xl border border-border dark:border-border-dark bg-surface dark:bg-surface-dark p-3 shadow-float">
      <div className="flex items-start gap-2">
        <PillBadge tone={event.severity === 'error' ? 'error' : 'warning'} size="regular">
          {TYPE_LABEL[event.type]}
        </PillBadge>
        <PillBadge tone="neutral" size="regular">{SOURCE_LABEL[event.source]}</PillBadge>
        <time className="ml-auto shrink-0 text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
          {formatTime(event.ts)}
        </time>
      </div>

      <div className="mt-2 break-words text-sm font-medium text-text-primary dark:text-text-primary-dark">
        {event.summary}
      </div>
      {event.lag_ms != null && (
        <div className="mt-1 text-xs text-text-secondary dark:text-text-secondary-dark">
          调度延迟：{isWedge || event.lower_bound ? '≥' : ''}{Math.round(event.lag_ms)}ms
        </div>
      )}
      {event.location && (
        <div className="mt-2 rounded-md bg-bg dark:bg-bg-dark px-2 py-1.5 font-mono text-[11px] break-all text-text-secondary dark:text-text-secondary-dark">
          {event.location}
        </div>
      )}

      {(event.request_id || event.conversation_id || event.message_id || (event.active_message_ids?.length ?? 0) > 0) && (
        <div className="mt-2 space-y-0.5 font-mono text-[10px] text-text-tertiary dark:text-text-tertiary-dark">
          {event.request_id && <div>request: {event.request_id}</div>}
          {event.conversation_id && <div>conversation: {event.conversation_id}</div>}
          {event.message_id && <div>message: {event.message_id}</div>}
          {(event.active_message_ids?.length ?? 0) > 0 && (
            <div>active messages: {event.active_message_ids.join(', ')}</div>
          )}
        </div>
      )}

      {(event.metrics_before || event.metrics_after) && (
        <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
          <div className="rounded-md bg-bg dark:bg-bg-dark p-2">
            <div className="text-text-tertiary dark:text-text-tertiary-dark">事件前 · {formatTime(event.metrics_before?.ts)}</div>
            <div className="mt-1 text-text-secondary dark:text-text-secondary-dark">{metricText(event.metrics_before)}</div>
          </div>
          <div className="rounded-md bg-bg dark:bg-bg-dark p-2">
            <div className="text-text-tertiary dark:text-text-tertiary-dark">事件后 · {formatTime(event.metrics_after?.ts)}</div>
            <div className="mt-1 text-text-secondary dark:text-text-secondary-dark">{metricText(event.metrics_after)}</div>
          </div>
        </div>
      )}

      {event.detail && (
        <details className="mt-3">
          <summary className="cursor-pointer select-none text-xs text-accent">查看错误堆栈</summary>
          <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md bg-bg dark:bg-bg-dark p-2 text-[10px] leading-4 text-text-secondary dark:text-text-secondary-dark">
            {event.detail}
          </pre>
        </details>
      )}
      <StackGroups event={event} />

      {event.conversation_id && (
        <button
          onClick={() => onOpenConversation(event.conversation_id as string)}
          className="mt-3 text-xs font-medium text-accent hover:underline"
        >
          在会话监控中打开
        </button>
      )}
    </article>
  );
}

export default function InstanceEventDrawer({
  instance,
  initialFilter,
  onClose,
  onOpenConversation,
}: Props) {
  const [data, setData] = useState<AdminInstanceEventsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<InstanceEventFilter>(initialFilter);
  const [reloadToken, setReloadToken] = useState(0);
  const { copied, copy } = useCopyFeedback();

  useEffect(() => setFilter(initialFilter), [initialFilter, instance.instance_id]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.getAdminInstanceEvents(instance.instance_id, filter, 50).then((response) => {
      if (!active) return;
      setData(response);
      setError(null);
    }).catch((reason) => {
      if (!active) return;
      setError(reason instanceof Error ? reason.message : '读取实例事件失败');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [filter, instance.instance_id, reloadToken]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const visibleEvents = useMemo(
    () => (data?.events ?? []).filter((event) => eventMatches(event, filter)),
    [data, filter],
  );
  const relevantSourceKeys = useMemo(() => {
    if (filter === 'error') return new Set(['error_log']);
    if (filter === 'wedge' || filter === 'loop_lag') return new Set(['loop_lag']);
    return new Set(['error_log', 'loop_lag']);
  }, [filter]);
  const unavailable = useMemo(
    () => Object.entries(data?.sources ?? {})
      .filter(([source, state]) => (
        relevantSourceKeys.has(source)
        && state.configured !== false
        && !state.available
      ))
      .map(([source]) => SOURCE_KEY_LABEL[source] ?? source),
    [data, relevantSourceKeys],
  );
  const truncationSourceKeys = useMemo(() => {
    const keys = new Set(relevantSourceKeys);
    if (filter === 'all' || filter === 'wedge' || filter === 'loop_lag') keys.add('metrics');
    return keys;
  }, [filter, relevantSourceKeys]);
  const truncated = useMemo(
    () => Object.entries(data?.sources ?? {})
      .filter(([source, state]) => truncationSourceKeys.has(source) && state.truncated)
      .map(([source]) => SOURCE_KEY_LABEL[source] ?? source),
    [data, truncationSourceKeys],
  );

  const handleCopy = useCallback(() => {
    void copy(serializeInstanceEvents(instance.instance_id, visibleEvents));
  }, [copy, instance.instance_id, visibleEvents]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/25" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${instance.instance_id} 实例事件详情`}
        className="flex h-full w-full max-w-xl flex-col border-l border-border dark:border-border-dark bg-chat dark:bg-chat-dark shadow-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-center gap-2 border-b border-border dark:border-border-dark px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-text-primary dark:text-text-primary-dark">实例事件详情</h2>
            <div className="truncate text-xs text-text-tertiary dark:text-text-tertiary-dark" title={instance.instance_id}>
              {instance.instance_id}
            </div>
          </div>
          <button
            onClick={handleCopy}
            className="ml-auto rounded-md px-2 py-1 text-xs text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-surface-dark"
          >
            {copied ? '已复制' : '复制诊断'}
          </button>
          <button
            onClick={() => setReloadToken((value) => value + 1)}
            className="rounded-md px-2 py-1 text-xs text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-surface-dark"
          >
            刷新
          </button>
          <button
            onClick={onClose}
            aria-label="关闭实例事件详情"
            className="rounded-md p-1.5 text-text-tertiary dark:text-text-tertiary-dark hover:bg-surface dark:hover:bg-surface-dark"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </header>

        <div className="border-b border-border dark:border-border-dark px-4 py-3">
          <HeartbeatSummary instance={instance} />
          <div className="mt-3 flex flex-wrap gap-1.5">
            {FILTERS.map((item) => (
              <button
                key={item.id}
                onClick={() => setFilter(item.id)}
                className={`rounded-full px-2.5 py-1 text-xs transition-colors ${
                  filter === item.id
                    ? 'bg-accent text-white'
                    : 'bg-surface dark:bg-surface-dark text-text-secondary dark:text-text-secondary-dark hover:text-accent'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && <div className="py-8 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">读取事件中…</div>}
          {error && (
            <div className="rounded-lg bg-status-error/10 p-3 text-sm text-status-error">{error}</div>
          )}
          {!loading && !error && unavailable.length > 0 && (
            <div className="mb-3 rounded-lg bg-status-warning/10 p-3 text-xs text-status-warning">
              当前应答节点无法读取部分来源：{unavailable.join('、')}。多主机本地盘部署时可能需要登录对应节点。
            </div>
          )}
          {!loading && !error && truncated.length > 0 && (
            <div className="mb-3 rounded-lg bg-surface dark:bg-surface-dark p-3 text-xs text-text-secondary dark:text-text-secondary-dark">
              {truncated.join('、')}超过单次扫描上限，当前仅展示最近保留范围。
            </div>
          )}
          {!loading && !error && visibleEvents.length === 0 && (
            <div className="py-8 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              当前保留范围内没有此类事件
            </div>
          )}
          <div className="space-y-3">
            {visibleEvents.map((event) => (
              <EventCard key={event.id} event={event} onOpenConversation={onOpenConversation} />
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}

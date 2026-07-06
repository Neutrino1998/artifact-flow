'use client';

import { useState, useEffect, useCallback } from 'react';
import * as api from '@/lib/api';
import type { InstanceHeartbeat, AdminInstancesResponse } from '@/lib/api';
import { parseUtcIso } from '@/lib/time';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { useUIStore } from '@/stores/uiStore';

// 实例监控面板轮询周期。心跳 sample 周期是 30s,面板 10s 轮询让状态色(尤其
// 陈旧→红)在心跳停更后一个 sample 周期内可见,又不过度打后端。
const POLL_MS = 10_000;

// status → 圆点色 / 文案。green=新鲜无异常;yellow=活着但有异常信号;red=陈旧/停更。
const STATUS_META: Record<string, { dot: string; label: string; text: string }> = {
  green: { dot: 'bg-status-success', label: '正常', text: 'text-status-success' },
  yellow: { dot: 'bg-status-warning', label: '告警', text: 'text-status-warning' },
  red: { dot: 'bg-status-error', label: '失联', text: 'text-status-error' },
};

// 相对时间:「Xs / Xm / Xh 前」;缺失返回 '—'。
function ago(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return '—';
  try {
    const then = parseUtcIso(iso).getTime();
    // parseUtcIso 对坏字符串返回 Invalid Date(不抛)→ getTime() 是 NaN,catch 够不着;
    // 显式挡一下,否则渲染成 "NaNd 前"。
    if (Number.isNaN(then)) return '—';
    const sec = Math.max(0, Math.round((nowMs - then) / 1000));
    if (sec < 60) return `${sec}s 前`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m 前`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h 前`;
    return `${Math.floor(sec / 86400)}d 前`;
  } catch {
    return '—';
  }
}

// 运行时长(started_at → now):「Xh Ym」/「Xd Yh」。
function uptime(iso: string | undefined, nowMs: number): string {
  if (!iso) return '—';
  try {
    const t = parseUtcIso(iso).getTime();
    if (Number.isNaN(t)) return '—';
    const sec = Math.max(0, Math.round((nowMs - t) / 1000));
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  } catch {
    return '—';
  }
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col min-w-0">
      <span className="text-[10px] uppercase tracking-wide text-text-tertiary dark:text-text-tertiary-dark whitespace-nowrap truncate">
        {label}
      </span>
      <span className={`text-sm tabular-nums whitespace-nowrap ${tone ?? 'text-text-primary dark:text-text-primary-dark'}`}>
        {value}
      </span>
    </div>
  );
}

function InstanceCard({ inst, nowMs, isSelf }: { inst: InstanceHeartbeat; nowMs: number; isSelf: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const meta = STATUS_META[inst.status] ?? STATUS_META.red;

  const loop = inst.loop_lag_ms ?? {};
  const proc = inst.process ?? {};
  const wedge = inst.last_wedge ?? null;
  const heal = inst.last_autoheal ?? null;
  const errCount = inst.error_count ?? 0;

  return (
    <div className="rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark p-3.5">
      {/* Header: status dot + id + version + self marker */}
      <div className="flex items-center gap-2">
        <span className={`inline-block w-2.5 h-2.5 rounded-full ${meta.dot} ${inst.status === 'red' ? '' : 'shadow-sm'}`} />
        <span className="font-medium text-text-primary dark:text-text-primary-dark truncate" title={inst.instance_id}>
          {inst.instance_id}
        </span>
        {isSelf && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent shrink-0">本机</span>
        )}
        <span className={`text-xs ml-auto shrink-0 ${meta.text}`}>{meta.label}</span>
      </div>

      {/* Sub-header: version + heartbeat freshness + uptime */}
      <div className="mt-1 flex items-center gap-3 text-xs text-text-secondary dark:text-text-secondary-dark">
        <span title="镜像版本">{inst.version ?? 'dev'}</span>
        <span title="最近心跳">心跳 {ago(inst.ts, nowMs)}</span>
        <span title="运行时长">上线 {uptime(inst.started_at, nowMs)}</span>
      </div>

      {/* Metrics grid — 数值 tile 一律中性色,健康颜色只由后端算好的 status 圆点承载
          (前端不再自判阈:旧的 loop≥500 高亮复制了 LOOP_LAG_WARN_MS、errCount>0 红
          又与窗口化的绿点矛盾——lifetime 计数 hours 后仍红。单点归后端 = by-construction
          消除两处漂移)。*/}
      <div className="mt-3 grid grid-cols-9 gap-3">
        {/* loop 值(p50/max)比其它 tile 长,给它 1.5 倍宽(3 份)防折行挤歪整排;
            其它三个各 2 份 → 3 + 2×3 = 9。 */}
        <div className="col-span-2 min-w-0">
          <Metric label="RSS" value={proc.rss_mb != null ? `${proc.rss_mb}M` : '—'} />
        </div>
        <div className="col-span-3 min-w-0">
          <Metric
            label="loop p50/max"
            value={`${loop.p50_ms ?? '—'}/${loop.max_1m_ms ?? '—'}`}
          />
        </div>
        <div className="col-span-2 min-w-0">
          <Metric label="在途" value={String(inst.in_flight ?? 0)} />
        </div>
        <div className="col-span-2 min-w-0">
          <Metric label="ERROR" value={String(errCount)} />
        </div>
      </div>

      {/* Anomaly badges */}
      {(wedge || heal) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {wedge && (
            <span className="text-[11px] px-2 py-0.5 rounded bg-status-warning/10 text-status-warning">
              watchdog 抓到 wedge · {ago(wedge.ts, nowMs)}{wedge.lag_ms != null ? ` · ${Math.round(wedge.lag_ms)}ms` : ''}
            </span>
          )}
          {heal && (
            <span className="text-[11px] px-2 py-0.5 rounded bg-status-error/10 text-status-error">
              autoheal 重启 ×{heal.count ?? 1} · {ago(heal.ts, nowMs)}
            </span>
          )}
        </div>
      )}

      {/* Expandable detail */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="mt-2.5 text-xs text-text-tertiary dark:text-text-tertiary-dark hover:text-accent transition-colors"
      >
        {expanded ? '收起详情' : '展开详情'}
      </button>
      {expanded && (
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-text-secondary dark:text-text-secondary-dark">
          <span>CPU: {proc.cpu_pct != null ? `${proc.cpu_pct}%` : '—'}</span>
          <span>FDs: {proc.open_fds ?? '—'}</span>
          <span>DB pool: {inst.db_pool ? `${inst.db_pool.in_use ?? 0}/${inst.db_pool.size ?? 0}${inst.db_pool.overflow ? ` +${inst.db_pool.overflow}` : ''}` : '—'}</span>
          <span>Redis: {inst.redis?.used_mb != null ? `${inst.redis.used_mb}M` : '—'}</span>
          <span>长跑任务：{inst.tasks_long_running ?? 0}</span>
          <span>data/: {inst.data_dir_mb != null ? `${inst.data_dir_mb}M` : '—'}</span>
          <span>最近 ERROR：{ago(inst.last_error_ts, nowMs)}</span>
          <span>started_at: {inst.started_at ?? '—'}</span>
        </div>
      )}
    </div>
  );
}

export default function InstancePanel() {
  const [data, setData] = useState<AdminInstancesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const claim = useLatestOnly();
  // 刷新按钮已上移到侧栏(与会话监控一致);bump 这个 tick 触发一次 reload。
  const refreshTick = useUIStore((s) => s.instancesRefreshTick);

  const load = useCallback(async () => {
    const isLatest = claim();
    try {
      const res = await api.getAdminInstances();
      if (!isLatest()) return;
      setData(res);
      setError(null);
      setNowMs(Date.now());
    } catch (e) {
      if (!isLatest()) return;
      setError(e instanceof Error ? e.message : '读取实例列表失败');
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load, refreshTick]);

  const instances = data?.instances ?? [];
  const selfId = data?.instance_id;

  return (
    <div className="flex-1 flex flex-col bg-chat dark:bg-chat-dark overflow-hidden">
      {/* Header — 刷新按钮已上移到侧栏(触发 instancesRefreshTick),这里不再自带 */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-border dark:border-border-dark">
        <div className="flex items-baseline gap-2 min-w-0">
          <h2 className="text-base font-semibold text-text-primary dark:text-text-primary-dark">实例监控</h2>
          <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
            {data ? (data.shared ? `多实例 · ${instances.length} 个` : '单机本地视图') : ''}
          </span>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading && !data && (
          <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark py-8 text-center">加载中…</div>
        )}
        {error && (
          <div className="text-sm text-status-error py-3">{error}</div>
        )}
        {!loading && !error && instances.length === 0 && (
          <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark py-8 text-center">
            暂无实例心跳
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {instances.map((inst) => (
            <InstanceCard key={inst.instance_id} inst={inst} nowMs={nowMs} isSelf={inst.instance_id === selfId} />
          ))}
        </div>
      </div>
    </div>
  );
}

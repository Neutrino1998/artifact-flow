'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import type { SiteNotification } from '@/types';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { useUIStore } from '@/stores/uiStore';
import DangerConfirmModal, { DangerConfirmTarget } from '@/components/layout/DangerConfirmModal';
import MarkdownBlock from '@/components/markdown/MarkdownBlock';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import { StatusNotice } from '@/components/ui/StatusNotice';
import { SwitchTrack } from '@/components/ui/SwitchTrack';
import { BUTTON_DANGER_OUTLINE, MENU_ROW_HOVER } from '@/lib/styles';

type Severity = SiteNotification['severity'];
type PreviewMode = 'edit' | 'preview';

const severityOptions = [
  { value: 'info', label: 'info' },
  { value: 'warn', label: 'warn' },
  { value: 'critical', label: 'critical' },
] as const;

const previewOptions = [
  { value: 'edit', label: '编辑' },
  { value: 'preview', label: '预览' },
] as const;

const severityDot: Record<Severity, string> = {
  info: 'bg-accent',
  warn: 'bg-status-warning',
  critical: 'bg-status-error',
};

function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.5 4h11M6 4V2.5h4V4M5 6.5v5M8 6.5v5M11 6.5v5M4 4l.5 10h7L12 4" />
    </svg>
  );
}

function newNotification(existing: SiteNotification[]): SiteNotification {
  const base = `notice-${new Date().toISOString().slice(0, 10)}`;
  const ids = new Set(existing.map((n) => n.id));
  let id = base;
  let i = 1;
  while (ids.has(id)) {
    i += 1;
    id = `${base}-${i}`;
  }
  return {
    id,
    severity: 'info',
    title: '',
    body: '',
    starts_at: null,
    ends_at: null,
    dismissible: true,
  };
}

function normalizeItems(items: SiteNotification[]): SiteNotification[] {
  return items.map((n) => ({
    id: n.id.trim(),
    severity: n.severity,
    title: n.title.trim(),
    body: n.body.trim(),
    starts_at: n.starts_at?.trim() || null,
    ends_at: n.ends_at?.trim() || null,
    dismissible: n.dismissible ?? true,
  }));
}

function validateItems(items: SiteNotification[]): string | null {
  const seen = new Set<string>();
  for (const n of items) {
    if (!n.id.trim()) return '通知 ID 不能为空';
    if (!n.title.trim()) return `通知 ${n.id || '(未命名)'} 缺少标题`;
    if (!n.body.trim()) return `通知 ${n.id} 缺少正文`;
    if (seen.has(n.id.trim())) return `通知 ID 重复：${n.id.trim()}`;
    seen.add(n.id.trim());

    const starts = n.starts_at?.trim();
    const ends = n.ends_at?.trim();
    const startsMs = starts ? Date.parse(starts) : undefined;
    const endsMs = ends ? Date.parse(ends) : undefined;
    if (starts && Number.isNaN(startsMs)) return `${n.id} 的开始时间格式无效`;
    if (ends && Number.isNaN(endsMs)) return `${n.id} 的结束时间格式无效`;
  }
  return null;
}

export default function NotificationConfigPanel() {
  const createRequestId = useUIStore((s) => s.notificationConfigCreateRequestId);
  const refreshRequestId = useUIStore((s) => s.notificationConfigRefreshRequestId);
  const saveRequestId = useUIStore((s) => s.notificationConfigSaveRequestId);
  const setNotificationConfigStatus = useUIStore((s) => s.setNotificationConfigStatus);
  const [items, setItems] = useState<SiteNotification[]>([]);
  const [revision, setRevision] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewMode, setPreviewMode] = useState<PreviewMode>('edit');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const claim = useLatestOnly();
  const handledCreateRequestId = useRef(createRequestId);
  const handledRefreshRequestId = useRef(refreshRequestId);
  const handledSaveRequestId = useRef(saveRequestId);

  const selected = useMemo(
    () => (selectedIndex === null ? null : items[selectedIndex] ?? null),
    [items, selectedIndex],
  );

  const load = useCallback(async () => {
    const isLatest = claim();
    setLoading(true);
    setError(null);
    try {
      const res = await api.getSiteNotifications();
      if (!isLatest()) return;
      setItems(res.notifications);
      setRevision(res.revision);
      setSelectedIndex((current) => (
        current !== null && current < res.notifications.length
          ? current
          : res.notifications.length > 0 ? 0 : null
      ));
      setDirty(false);
      setMessage(null);
    } catch (err) {
      if (!isLatest()) return;
      setError(err instanceof ApiError ? err.message : '加载通知配置失败');
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateSelected = useCallback((patch: Partial<SiteNotification>) => {
    if (selectedIndex === null) return;
    setItems((prev) => prev.map((n, index) => (
      index === selectedIndex ? { ...n, ...patch } : n
    )));
    setDirty(true);
    setMessage(null);
    setError(null);
  }, [selectedIndex]);

  const handleAdd = useCallback(() => {
    const next = newNotification(items);
    setItems((prev) => [...prev, next]);
    setSelectedIndex(items.length);
    setDirty(true);
    setMessage(null);
    setError(null);
    setPreviewMode('edit');
  }, [items]);

  const handleConfirmDelete = useCallback(() => {
    if (selectedIndex === null) return;
    const next = items.filter((_, index) => index !== selectedIndex);
    setItems(next);
    setSelectedIndex(next.length === 0 ? null : Math.min(selectedIndex, next.length - 1));
    setDirty(true);
    setMessage(null);
    setError(null);
    setConfirmDelete(false);
  }, [items, selectedIndex]);

  const handleRefresh = useCallback(() => {
    void load();
  }, [load]);

  const handleSave = useCallback(async () => {
    if (!dirty || saving) return;
    const normalized = normalizeItems(items);
    const validationError = validateItems(normalized);
    if (validationError) {
      setError(validationError);
      setMessage(null);
      return;
    }
    if (revision === null) {
      setError('请先刷新通知配置后再保存');
      setMessage(null);
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const res = await api.updateSiteNotifications({
        notifications: normalized,
        expected_revision: revision,
      });
      setItems(res.notifications);
      setRevision(res.revision);
      setSelectedIndex((current) => (
        current !== null && current < res.notifications.length
          ? current
          : res.notifications.length > 0 ? 0 : null
      ));
      setDirty(false);
      setMessage('已保存');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存通知配置失败');
    } finally {
      setSaving(false);
    }
  }, [dirty, items, revision, saving]);

  useEffect(() => {
    setNotificationConfigStatus({ dirty, saving, loading });
  }, [dirty, loading, saving, setNotificationConfigStatus]);

  useEffect(() => () => {
    setNotificationConfigStatus({ dirty: false, saving: false, loading: false });
  }, [setNotificationConfigStatus]);

  useEffect(() => {
    if (createRequestId === handledCreateRequestId.current) return;
    handledCreateRequestId.current = createRequestId;
    handleAdd();
  }, [createRequestId, handleAdd]);

  useEffect(() => {
    if (refreshRequestId === handledRefreshRequestId.current) return;
    handledRefreshRequestId.current = refreshRequestId;
    handleRefresh();
  }, [handleRefresh, refreshRequestId]);

  useEffect(() => {
    if (saveRequestId === handledSaveRequestId.current) return;
    handledSaveRequestId.current = saveRequestId;
    void handleSave();
  }, [handleSave, saveRequestId]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <div className="flex-1 min-h-0 overflow-hidden px-4 py-4">
        <div className="max-w-6xl mx-auto h-full min-h-0 flex flex-col gap-3">
          {error && (
            <StatusNotice tone="error" onDismiss={() => setError(null)}>
              {error}
            </StatusNotice>
          )}
          {message && !error && (
            <StatusNotice tone="success" onDismiss={() => setMessage(null)}>
              {message}
            </StatusNotice>
          )}

          <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[18rem_1fr] gap-4">
            <aside className="min-h-0 flex flex-col rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark overflow-hidden">
              <div className="px-3 py-2 border-b border-border dark:border-border-dark flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-text-primary dark:text-text-primary-dark">通知</div>
                  <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">{items.length}/50</div>
                </div>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto p-2">
                {loading && items.length === 0 ? (
                  <div className="py-10 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
                    加载中...
                  </div>
                ) : items.length === 0 ? (
                  <div className="py-10 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
                    暂无通知
                  </div>
                ) : (
                  items.map((item, index) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedIndex(index)}
                      className={`w-full text-left rounded-lg px-3 py-2.5 mb-1 transition-colors ${
                        index === selectedIndex
                          ? 'bg-panel-accent dark:bg-panel-accent-dark'
                          : MENU_ROW_HOVER
                      }`}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${severityDot[item.severity]}`} />
                        <span className="font-medium text-sm text-text-primary dark:text-text-primary-dark truncate">
                          {item.title || '未命名通知'}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
                        {item.id}
                      </div>
                    </button>
                  ))
                )}
              </div>
            </aside>

            <main className="min-h-0 rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark overflow-hidden">
              {selected ? (
                <div className="h-full min-h-0 overflow-y-auto p-4">
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                    <label className="flex flex-col gap-1.5 text-sm">
                      <span className="font-medium text-text-primary dark:text-text-primary-dark">ID</span>
                      <input
                        type="text"
                        value={selected.id}
                        onChange={(e) => updateSelected({ id: e.target.value })}
                        className="rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-text-primary dark:text-text-primary-dark outline-none focus:border-accent"
                      />
                    </label>

                    <label className="flex flex-col gap-1.5 text-sm">
                      <span className="font-medium text-text-primary dark:text-text-primary-dark">标题</span>
                      <input
                        type="text"
                        value={selected.title}
                        onChange={(e) => updateSelected({ title: e.target.value })}
                        className="rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-text-primary dark:text-text-primary-dark outline-none focus:border-accent"
                      />
                    </label>

                    <label className="flex flex-col gap-1.5 text-sm">
                      <span className="font-medium text-text-primary dark:text-text-primary-dark">开始时间</span>
                      <input
                        type="text"
                        value={selected.starts_at ?? ''}
                        onChange={(e) => updateSelected({ starts_at: e.target.value })}
                        placeholder="2026-05-15 00:00"
                        className="rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none focus:border-accent"
                      />
                    </label>

                    <label className="flex flex-col gap-1.5 text-sm">
                      <span className="font-medium text-text-primary dark:text-text-primary-dark">结束时间</span>
                      <input
                        type="text"
                        value={selected.ends_at ?? ''}
                        onChange={(e) => updateSelected({ ends_at: e.target.value })}
                        placeholder="2026-05-20 04:00"
                        className="rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none focus:border-accent"
                      />
                    </label>

                    <div className="flex flex-col gap-2 rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2.5 text-sm sm:flex-row sm:items-center sm:justify-between">
                      <span className="min-w-0">
                        <span className="block font-medium text-text-primary dark:text-text-primary-dark">级别</span>
                        <span className="mt-0.5 block text-xs text-text-tertiary dark:text-text-tertiary-dark">
                          控制通知横幅的提示强度
                        </span>
                      </span>
                      <SegmentedTabs
                        value={selected.severity}
                        options={severityOptions}
                        onChange={(severity) => updateSelected({ severity })}
                        ariaLabel="通知级别"
                        className="self-start sm:self-center"
                      />
                    </div>

                    <button
                      type="button"
                      onClick={() => updateSelected({ dismissible: !(selected.dismissible ?? true) })}
                      className="flex items-center justify-between gap-3 rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-sm"
                    >
                      <span className="font-medium text-text-primary dark:text-text-primary-dark">允许关闭</span>
                      <SwitchTrack checked={selected.dismissible ?? true} />
                    </button>
                  </div>

                  <div className="mt-4">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-medium text-text-primary dark:text-text-primary-dark">正文</span>
                      <SegmentedTabs
                        value={previewMode}
                        options={previewOptions}
                        onChange={setPreviewMode}
                        ariaLabel="通知正文视图"
                      />
                    </div>
                    {previewMode === 'edit' ? (
                      <label className="flex flex-col text-sm">
                        <textarea
                          value={selected.body}
                          onChange={(e) => updateSelected({ body: e.target.value })}
                          rows={16}
                          className="min-h-80 resize-y rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 font-mono text-sm text-text-primary dark:text-text-primary-dark outline-none focus:border-accent"
                        />
                      </label>
                    ) : (
                      <div className="min-h-80 rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-4 py-3">
                        <MarkdownBlock className="prose prose-sm dark:prose-invert max-w-none text-text-secondary dark:text-text-secondary-dark">
                          {selected.body || ' '}
                        </MarkdownBlock>
                      </div>
                    )}
                  </div>

                  <div className="mt-4 flex justify-end">
                    <button
                      type="button"
                      onClick={() => setConfirmDelete(true)}
                      className={`${BUTTON_DANGER_OUTLINE} inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm`}
                    >
                      <TrashIcon />
                      删除通知
                    </button>
                  </div>
                </div>
              ) : (
                <div className="h-full flex items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
                  选择或新增通知
                </div>
              )}
            </main>
          </div>
        </div>
      </div>

      {confirmDelete && selected && (
        <DangerConfirmModal
          title="删除通知"
          message="将从通知配置中移除此通知。删除后仍需保存配置才会生效。"
          confirmLabel="确认删除"
          onCancel={() => setConfirmDelete(false)}
          onConfirm={handleConfirmDelete}
        >
          <DangerConfirmTarget
            name={selected.title || selected.id || '未命名通知'}
            description={selected.id}
          />
        </DangerConfirmModal>
      )}
    </div>
  );
}

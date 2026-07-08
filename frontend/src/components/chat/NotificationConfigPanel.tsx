'use client';

import { useCallback, useEffect, useMemo, useRef } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import type { SiteNotification } from '@/types';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { useUIStore } from '@/stores/uiStore';
import { useNotificationConfigStore, type NotificationPreviewMode } from '@/stores/notificationConfigStore';
import DangerConfirmModal, { DangerConfirmTarget } from '@/components/layout/DangerConfirmModal';
import MarkdownBlock from '@/components/markdown/MarkdownBlock';
import { SegmentedTabs, type SegmentedTabOption } from '@/components/ui/SegmentedTabs';
import { StatusNotice } from '@/components/ui/StatusNotice';
import { SwitchTrack } from '@/components/ui/SwitchTrack';
import { BUTTON_DANGER_OUTLINE, INPUT_ON_PANEL } from '@/lib/styles';

type Severity = SiteNotification['severity'];

const severityOptions: readonly SegmentedTabOption<Severity>[] = [
  { value: 'info', label: 'info' },
  { value: 'warn', label: 'warn' },
  { value: 'critical', label: 'critical' },
] as const;

const previewOptions: readonly SegmentedTabOption<NotificationPreviewMode>[] = [
  { value: 'edit', label: '编辑' },
  { value: 'preview', label: '预览' },
] as const;

function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.5 4h11M6 4V2.5h4V4M5 6.5v5M8 6.5v5M11 6.5v5M4 4l.5 10h7L12 4" />
    </svg>
  );
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
  const items = useNotificationConfigStore((s) => s.items);
  const revision = useNotificationConfigStore((s) => s.revision);
  const selectedIndex = useNotificationConfigStore((s) => s.selectedIndex);
  const loading = useNotificationConfigStore((s) => s.loading);
  const saving = useNotificationConfigStore((s) => s.saving);
  const dirty = useNotificationConfigStore((s) => s.dirty);
  const message = useNotificationConfigStore((s) => s.message);
  const error = useNotificationConfigStore((s) => s.error);
  const previewMode = useNotificationConfigStore((s) => s.previewMode);
  const confirmDelete = useNotificationConfigStore((s) => s.confirmDelete);
  const setLoaded = useNotificationConfigStore((s) => s.setLoaded);
  const setLoading = useNotificationConfigStore((s) => s.setLoading);
  const setSaving = useNotificationConfigStore((s) => s.setSaving);
  const setError = useNotificationConfigStore((s) => s.setError);
  const setMessage = useNotificationConfigStore((s) => s.setMessage);
  const setPreviewMode = useNotificationConfigStore((s) => s.setPreviewMode);
  const setConfirmDelete = useNotificationConfigStore((s) => s.setConfirmDelete);
  const addNotification = useNotificationConfigStore((s) => s.addNotification);
  const updateSelected = useNotificationConfigStore((s) => s.updateSelected);
  const deleteSelected = useNotificationConfigStore((s) => s.deleteSelected);
  const claim = useLatestOnly();
  const mountedRef = useRef(false);
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
      if (!mountedRef.current || !isLatest()) return;
      setLoaded(res.notifications, res.revision);
    } catch (err) {
      if (!mountedRef.current || !isLatest()) return;
      setError(err instanceof ApiError ? err.message : '加载通知配置失败');
    } finally {
      if (mountedRef.current && isLatest()) setLoading(false);
    }
  }, [claim, setError, setLoaded, setLoading]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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
      if (!mountedRef.current) return;
      setLoaded(res.notifications, res.revision);
      setMessage('已保存');
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof ApiError ? err.message : '保存通知配置失败');
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  }, [dirty, items, revision, saving, setError, setLoaded, setMessage, setSaving]);

  useEffect(() => {
    setNotificationConfigStatus({ dirty, saving, loading });
  }, [dirty, loading, saving, setNotificationConfigStatus]);

  useEffect(() => () => {
    setNotificationConfigStatus({ dirty: false, saving: false, loading: false });
  }, [setNotificationConfigStatus]);

  useEffect(() => {
    if (createRequestId === handledCreateRequestId.current) return;
    handledCreateRequestId.current = createRequestId;
    addNotification();
  }, [addNotification, createRequestId]);

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
      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
        <div className="max-w-4xl mx-auto min-h-full flex flex-col gap-4">
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

          {selected ? (
            <div className="space-y-4">
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-text-primary dark:text-text-primary-dark">ID</span>
                  <input
                    type="text"
                    value={selected.id}
                    onChange={(e) => updateSelected({ id: e.target.value })}
                    className={INPUT_ON_PANEL}
                  />
                </label>

                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-text-primary dark:text-text-primary-dark">标题</span>
                  <input
                    type="text"
                    value={selected.title}
                    onChange={(e) => updateSelected({ title: e.target.value })}
                    className={INPUT_ON_PANEL}
                  />
                </label>

                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-text-primary dark:text-text-primary-dark">开始时间</span>
                  <input
                    type="text"
                    value={selected.starts_at ?? ''}
                    onChange={(e) => updateSelected({ starts_at: e.target.value })}
                    placeholder="2026-05-15 00:00"
                    className={INPUT_ON_PANEL}
                  />
                </label>

                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-text-primary dark:text-text-primary-dark">结束时间</span>
                  <input
                    type="text"
                    value={selected.ends_at ?? ''}
                    onChange={(e) => updateSelected({ ends_at: e.target.value })}
                    placeholder="2026-05-20 04:00"
                    className={INPUT_ON_PANEL}
                  />
                </label>

                <div className="flex flex-col gap-2 rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark px-3 py-2.5 text-sm focus-within:border-accent dark:focus-within:border-accent sm:flex-row sm:items-center sm:justify-between">
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
                  className="flex items-center justify-between gap-3 rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark px-3 py-2 text-sm focus:outline-none focus:border-accent dark:focus:border-accent"
                >
                  <span className="font-medium text-text-primary dark:text-text-primary-dark">允许关闭</span>
                  <SwitchTrack checked={selected.dismissible ?? true} />
                </button>
              </div>

              <div>
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
                      className={`${INPUT_ON_PANEL} min-h-80 resize-y font-mono text-sm`}
                    />
                  </label>
                ) : (
                  <div className="min-h-80 rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark px-4 py-3">
                    <MarkdownBlock className="prose prose-sm dark:prose-invert max-w-none text-text-secondary dark:text-text-secondary-dark">
                      {selected.body || ' '}
                    </MarkdownBlock>
                  </div>
                )}
              </div>

              <div className="flex justify-end">
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
            <div className="flex-1 flex items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              {loading ? '加载中...' : '从左栏选择或新增通知'}
            </div>
          )}
        </div>
      </div>

      {confirmDelete && selected && (
        <DangerConfirmModal
          title="删除通知"
          message="将从通知配置中移除此通知。删除后仍需保存配置才会生效。"
          confirmLabel="确认删除"
          onCancel={() => setConfirmDelete(false)}
          onConfirm={deleteSelected}
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

'use client';

import { useState, useCallback, useEffect } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from '@/lib/styles';
import DepartmentCascader from '@/components/forms/DepartmentCascader';
import DangerConfirmModal from '@/components/layout/DangerConfirmModal';
import type { BulkActionResponse, BulkImpactResponse } from '@/types';

type ActionMode = 'idle' | 'set-department' | 'confirm-delete';

export default function BulkActionPanel() {
  const selection = useUIStore((s) => s.userManagementSelection);
  const exitSelectionMode = useUIStore((s) => s.exitSelectionMode);
  const bumpListVersion = useUIStore((s) => s.bumpUserMgmtListVersion);

  const [mode, setMode] = useState<ActionMode>('idle');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<BulkActionResponse | null>(null);

  // set-department state
  const [pendingDeptId, setPendingDeptId] = useState<string | null>(null);

  // confirm-delete state
  const [impact, setImpact] = useState<BulkImpactResponse | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);

  // 选中数变化时清掉上一次结果（避免 stale "succeeded 5" 显示在新选中集旁）
  useEffect(() => {
    setLastResult(null);
    setError(null);
  }, [selection]);

  const runSimpleAction = useCallback(async (action: 'disable' | 'enable') => {
    if (selection.length === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    setLastResult(null);
    try {
      const res = await api.bulkUserAction({
        ids: selection,
        action,
        payload: null,
      });
      setLastResult(res);
      bumpListVersion();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : '操作失败');
    } finally {
      setSubmitting(false);
    }
  }, [selection, submitting, bumpListVersion]);

  const handleSetDepartment = useCallback(async () => {
    if (selection.length === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    setLastResult(null);
    try {
      const res = await api.bulkUserAction({
        ids: selection,
        action: 'set_department',
        payload: { department_id: pendingDeptId },
      });
      setLastResult(res);
      bumpListVersion();
      setMode('idle');
      setPendingDeptId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : '改部门失败');
    } finally {
      setSubmitting(false);
    }
  }, [selection, submitting, pendingDeptId, bumpListVersion]);

  const handleStartDelete = useCallback(async () => {
    if (selection.length === 0) return;
    setError(null);
    setMode('confirm-delete');
    setImpactLoading(true);
    try {
      const res = await api.getUsersBulkImpact(selection);
      setImpact(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : '加载影响数据失败');
      setImpact(null);
    } finally {
      setImpactLoading(false);
    }
  }, [selection]);

  const handleConfirmDelete = useCallback(async () => {
    const res = await api.bulkUserAction({
      ids: selection,
      action: 'delete',
      payload: null,
    });
    setLastResult(res);
    bumpListVersion();
    setMode('idle');
    setImpact(null);
    // 删除成功直接退出选择模式（被删的 id 不再有意义留在 selection 里）
    if (res.failed.length === 0) {
      exitSelectionMode();
    } else {
      // 有失败：清掉成功的，保留失败的让用户看
      const succeededSet = new Set(res.succeeded);
      const remaining = selection.filter((id) => !succeededSet.has(id));
      useUIStore.getState().setUserManagementSelection(remaining);
    }
  }, [selection, bumpListVersion, exitSelectionMode]);

  const empty = selection.length === 0;

  return (
    <div className="flex-1 overflow-y-auto bg-chat dark:bg-chat-dark">
      <div className="max-w-md mx-auto px-6 py-6">
        <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark mb-1">
          批量操作
        </h2>
        <p className="text-sm text-text-secondary dark:text-text-secondary-dark mb-6">
          已选 <span className="text-text-primary dark:text-text-primary-dark font-medium">{selection.length}</span> 个用户
          {empty && <span className="ml-2 text-text-tertiary">— 在左侧勾选用户后再选动作</span>}
        </p>

        {error && (
          <div role="alert" className="mb-4 px-3 py-2 text-sm text-status-error bg-status-error/10 border border-status-error/30 rounded-lg">
            {error}
          </div>
        )}

        {lastResult && (
          <div className="mb-4 px-3 py-2 text-sm rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark">
            <div className="text-text-primary dark:text-text-primary-dark">
              成功 <span className="font-medium text-status-success dark:text-status-success-dark">{lastResult.succeeded.length}</span>
              {lastResult.failed.length > 0 && (
                <>
                  ，失败 <span className="font-medium text-status-error">{lastResult.failed.length}</span>
                </>
              )}
            </div>
            {lastResult.failed.length > 0 && (
              <ul className="mt-2 max-h-40 overflow-y-auto text-xs text-text-secondary dark:text-text-secondary-dark space-y-0.5">
                {lastResult.failed.map((f) => (
                  <li key={f.id}>
                    <span className="opacity-60">{f.id}</span>
                    <span className="ml-2 text-status-error">{f.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {mode === 'idle' && (
          <div className="space-y-2">
            <button
              onClick={() => runSimpleAction('enable')}
              disabled={empty || submitting}
              className={`${BUTTON_SECONDARY} w-full rounded-lg px-4 py-2.5 text-left`}
            >
              <div className="font-medium">启用</div>
              <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">将选中用户设为活跃</div>
            </button>
            <button
              onClick={() => runSimpleAction('disable')}
              disabled={empty || submitting}
              className={`${BUTTON_SECONDARY} w-full rounded-lg px-4 py-2.5 text-left`}
            >
              <div className="font-medium">禁用</div>
              <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">禁用后用户无法登录；已在跑的 engine 不中断</div>
            </button>
            <button
              onClick={() => { setMode('set-department'); setPendingDeptId(null); }}
              disabled={empty || submitting}
              className={`${BUTTON_SECONDARY} w-full rounded-lg px-4 py-2.5 text-left`}
            >
              <div className="font-medium">改部门</div>
              <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">将选中用户分配到同一部门（或清空）</div>
            </button>
            <button
              onClick={handleStartDelete}
              disabled={empty || submitting}
              className="w-full px-4 py-2.5 rounded-lg border border-status-error/40 text-status-error hover:bg-status-error/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-left"
            >
              <div className="font-medium">删除</div>
              <div className="text-xs opacity-80">硬删用户 + 级联会话；操作不可恢复</div>
            </button>
          </div>
        )}

        {mode === 'set-department' && (
          <div className="space-y-3">
            <div className="text-sm text-text-secondary dark:text-text-secondary-dark">
              选择一个部门，或留空以清除归属：
            </div>
            <DepartmentCascader
              value={pendingDeptId}
              onChange={setPendingDeptId}
              allowCreate={false}
            />
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => { setMode('idle'); setPendingDeptId(null); }}
                disabled={submitting}
                className={`${BUTTON_SECONDARY} rounded-lg px-4 py-2`}
              >
                取消
              </button>
              <button
                onClick={handleSetDepartment}
                disabled={submitting}
                className={`${BUTTON_PRIMARY} flex-1 rounded-lg px-4 py-2`}
              >
                {submitting ? '处理中...' : pendingDeptId === null ? '清空部门' : '应用'}
              </button>
            </div>
          </div>
        )}
      </div>

      {mode === 'confirm-delete' && (
        <DangerConfirmModal
          title="批量删除用户"
          message={
            impactLoading
              ? '正在加载影响数据...'
              : impact
                ? `将删除 ${selection.length} 个用户、共 ${impact.conversation_count} 条会话。\n此操作不可恢复，关联的消息 / 事件 / artifacts 也会被级联删除。`
                : `将删除 ${selection.length} 个用户及其所有会话。\n此操作不可恢复。`
          }
          confirmLabel="删除"
          onConfirm={handleConfirmDelete}
          onCancel={() => { setMode('idle'); setImpact(null); }}
        />
      )}
    </div>
  );
}

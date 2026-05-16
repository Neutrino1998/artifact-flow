'use client';

import { useCallback, useEffect, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import {
  BUTTON_DANGER_OUTLINE,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  INPUT_ON_PANEL,
  LABEL_CLASS,
} from '@/lib/styles';
import type { DepartmentResponse } from '@/types';
import DangerConfirmModal from '@/components/layout/DangerConfirmModal';
import PanelShell from '@/components/layout/PanelShell';
import DepartmentCascader from '@/components/forms/DepartmentCascader';

interface DepartmentDetailFormProps {
  deptId: string;
  /** 父级 panel 的部门写入版本号 — bump 时本组件重拉详情 */
  refreshKey?: number;
  /** 删除/更新成功后的钩子，让父级刷新树并切回 tree 视图 */
  onChanged: () => void;
  onDeleted: () => void;
  onBack: () => void;
}

export default function DepartmentDetailForm({
  deptId,
  refreshKey,
  onChanged,
  onDeleted,
  onBack,
}: DepartmentDetailFormProps) {
  const [dept, setDept] = useState<DepartmentResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [parentId, setParentId] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const d = await api.getDepartment(deptId);
      setDept(d);
      setName(d.name);
      setParentId(d.parent_id ?? null);
      setSaveError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '加载部门失败');
    } finally {
      setLoading(false);
    }
  }, [deptId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const dirty = dept !== null && (name.trim() !== dept.name || parentId !== (dept.parent_id ?? null));
  const nameInvalid = name.trim().length === 0;

  const handleSave = async () => {
    if (!dept || !dirty || saving || nameInvalid) return;
    setSaving(true);
    setSaveError(null);
    try {
      // 改名 + 搬家是两个独立 API；按需依次调
      let updated = dept;
      const newName = name.trim();
      if (newName !== dept.name) {
        updated = await api.renameDepartment(dept.id, { name: newName });
      }
      if (parentId !== (dept.parent_id ?? null)) {
        updated = await api.moveDepartment(dept.id, { new_parent_id: parentId });
      }
      setDept(updated);
      onChanged();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!dept) return;
    await api.deleteDepartment(dept.id);
    onDeleted();
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-chat dark:bg-chat-dark p-6">
        <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark">加载中...</div>
      </div>
    );
  }

  if (loadError || !dept) {
    return (
      <div className="flex-1 flex flex-col gap-3 items-center justify-center bg-chat dark:bg-chat-dark p-6">
        <div className="text-sm text-status-error">{loadError ?? '部门不存在'}</div>
        <button onClick={load} className={`${BUTTON_SECONDARY} rounded-lg px-4 py-1.5`}>
          重试
        </button>
      </div>
    );
  }

  const canDelete = dept.user_count === 0 && dept.child_count === 0;
  const deleteDisabledReason = !canDelete
    ? `删除前需先迁走 ${dept.user_count} 个用户和 ${dept.child_count} 个子部门`
    : '';

  return (
    <PanelShell
      header={
        <div className="flex items-center justify-between gap-3">
          <button
            onClick={onBack}
            disabled={saving}
            className="flex items-center gap-1.5 text-sm text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark disabled:opacity-40 transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M9 3l-4 4 4 4" />
            </svg>
            返回部门树
          </button>
          <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
            {dept.user_count} 人 · {dept.child_count} 子部门
          </div>
        </div>
      }
      footer={
        <>
          <button
            onClick={() => setConfirmDelete(true)}
            disabled={saving || !canDelete}
            title={deleteDisabledReason || '删除部门'}
            className={`${BUTTON_DANGER_OUTLINE} rounded-lg px-5 py-2`}
          >
            删除
          </button>
          <button
            onClick={handleSave}
            disabled={!dirty || saving || nameInvalid}
            className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </>
      }
    >
      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        <div className="text-xs">
          <div className="text-text-tertiary dark:text-text-tertiary-dark">部门 ID</div>
          <div className="font-mono break-all text-text-secondary dark:text-text-secondary-dark">{dept.id}</div>
        </div>

        <div>
          <label className={LABEL_CLASS}>
            名称
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={saving}
            className={INPUT_ON_PANEL}
          />
          {nameInvalid && (
            <p className="text-status-error text-xs mt-1">名称不能为空</p>
          )}
        </div>

        <div>
          <label className={LABEL_CLASS}>
            父部门
            <span className="ml-2 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              （不选 = 顶级部门）
            </span>
          </label>
          <DepartmentCascader
            value={parentId}
            onChange={setParentId}
            excludeSubtreeOf={dept.id}
            disabled={saving}
            refreshKey={refreshKey}
          />
        </div>

        {saveError && (
          <div className="text-status-error text-sm">{saveError}</div>
        )}
      </div>

      {confirmDelete && (
        <DangerConfirmModal
          title="删除部门"
          message={`部门："${dept.name}"\n操作不可恢复。`}
          confirmLabel="确认删除"
          onCancel={() => setConfirmDelete(false)}
          onConfirm={handleDelete}
        />
      )}
    </PanelShell>
  );
}

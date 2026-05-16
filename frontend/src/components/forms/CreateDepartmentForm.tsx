'use client';

import { useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  INPUT_ON_PANEL,
  LABEL_CLASS,
} from '@/lib/styles';
import DepartmentCascader from '@/components/forms/DepartmentCascader';

interface CreateDepartmentFormProps {
  /** 默认父部门 id；null = 创建顶级部门 */
  defaultParentId: string | null;
  /** 父级 panel 的部门写入版本号 — bump 时 cascader 重拉树 */
  refreshKey?: number;
  onCreated: (newDeptId: string) => void;
  onBack: () => void;
}

export default function CreateDepartmentForm({
  defaultParentId,
  refreshKey,
  onCreated,
  onBack,
}: CreateDepartmentFormProps) {
  const [name, setName] = useState('');
  const [parentId, setParentId] = useState<string | null>(defaultParentId);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim().length > 0 && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const created = await api.createDepartment({
        name: name.trim(),
        parent_id: parentId,
      });
      onCreated(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <div className="px-6 pt-5 pb-3 border-b border-border dark:border-border-dark">
        <button
          onClick={onBack}
          disabled={submitting}
          className="flex items-center gap-1.5 text-sm text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark disabled:opacity-40 transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M9 3l-4 4 4 4" />
          </svg>
          返回部门树
        </button>
        <div className="text-base font-semibold text-text-primary dark:text-text-primary-dark mt-2">
          新建部门
        </div>
      </div>

      <form
        id="create-dept-form"
        onSubmit={handleSubmit}
        className="flex-1 overflow-y-auto px-6 py-5 space-y-4"
      >
        <div>
          <label className={LABEL_CLASS}>
            名称 <span className="text-status-error">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={submitting}
            autoFocus
            placeholder="部门名称"
            className={INPUT_ON_PANEL}
          />
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
            disabled={submitting}
            refreshKey={refreshKey}
          />
        </div>

        {error && (
          <div className="text-status-error text-sm">{error}</div>
        )}
      </form>

      <div className="border-t border-border dark:border-border-dark px-6 py-4 flex justify-end gap-3">
        <button
          onClick={onBack}
          disabled={submitting}
          type="button"
          className={`${BUTTON_SECONDARY} rounded-lg px-6 py-2`}
        >
          取消
        </button>
        <button
          form="create-dept-form"
          type="submit"
          disabled={!canSubmit}
          className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
        >
          {submitting ? '创建中...' : '创建'}
        </button>
      </div>
    </div>
  );
}

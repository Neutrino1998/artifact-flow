'use client';

import { useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  INPUT_ON_PANEL,
  LABEL_CLASS,
} from '@/lib/styles';
import DepartmentCascader from '@/components/forms/DepartmentCascader';
import PanelShell from '@/components/layout/PanelShell';

const ROLE_OPTIONS = [
  { value: 'user', label: 'user' },
  { value: 'admin', label: 'admin' },
];

export default function CreateUserForm() {
  const setRightView = useUIStore((s) => s.setUserManagementRightView);
  const bumpListVersion = useUIStore((s) => s.bumpUserMgmtListVersion);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<'user' | 'admin'>('user');
  const [departmentId, setDepartmentId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit =
    username.trim().length >= 2 &&
    password.length >= 4 &&
    !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      await api.createUser({
        username: username.trim(),
        password,
        display_name: displayName.trim() || null,
        role,
        department_id: departmentId,
      });
      bumpListVersion();
      setRightView({ type: 'empty' });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : '创建失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PanelShell
      header={
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-base font-semibold text-text-primary dark:text-text-primary-dark">
              新建用户
            </div>
            <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
              用户名只能包含字母、数字、点、下划线、连字符（2~64 字符）
            </div>
          </div>
          <button
            onClick={() => setRightView({ type: 'empty' })}
            disabled={submitting}
            className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark disabled:opacity-40 transition-colors"
            aria-label="关闭"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      }
      footer={
        <>
          <button
            onClick={() => setRightView({ type: 'empty' })}
            disabled={submitting}
            type="button"
            className={`${BUTTON_SECONDARY} rounded-lg px-6 py-2`}
          >
            取消
          </button>
          <button
            form="create-user-form"
            type="submit"
            disabled={!canSubmit}
            className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
          >
            {submitting ? '创建中...' : '创建'}
          </button>
        </>
      }
    >
      <form
        id="create-user-form"
        onSubmit={handleSubmit}
        className="flex-1 overflow-y-auto px-6 py-5 space-y-4"
      >
        <div>
          <label className={LABEL_CLASS}>
            用户名 <span className="text-status-error">*</span>
          </label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={submitting}
            autoFocus
            className={`${INPUT_ON_PANEL} font-mono`}
          />
        </div>

        <div>
          <label className={LABEL_CLASS}>
            密码 <span className="text-status-error">*</span>
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
            placeholder="至少 4 个字符"
            className={INPUT_ON_PANEL}
          />
        </div>

        <div>
          <label className={LABEL_CLASS}>
            显示名（可选）
          </label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            disabled={submitting}
            placeholder={username || '默认使用用户名'}
            className={INPUT_ON_PANEL}
          />
        </div>

        <div>
          <label className={LABEL_CLASS}>
            角色
          </label>
          <div className="relative">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as 'user' | 'admin')}
              disabled={submitting}
              className={`${INPUT_ON_PANEL} appearance-none pr-9`}
            >
              {ROLE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <svg
              className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary dark:text-text-tertiary-dark"
              width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
            >
              <path d="M3 4.5l3 3 3-3" />
            </svg>
          </div>
        </div>

        <div>
          <label className={LABEL_CLASS}>
            部门（可选）
          </label>
          <DepartmentCascader
            value={departmentId}
            onChange={setDepartmentId}
            allowCreate
            disabled={submitting}
          />
        </div>

        {error && (
          <div className="text-status-error text-sm">{error}</div>
        )}
      </form>
    </PanelShell>
  );
}

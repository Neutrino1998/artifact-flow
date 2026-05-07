'use client';

import { useEffect, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';

interface EditDisplayNameDialogProps {
  onClose: () => void;
}

export default function EditDisplayNameDialog({ onClose }: EditDisplayNameDialogProps) {
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const login = useAuthStore((s) => s.login);
  const bumpListVersion = useUIStore((s) => s.bumpUserMgmtListVersion);

  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ESC 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose, submitting]);

  const trimmed = displayName.trim();
  const original = user?.display_name ?? '';
  const dirty = trimmed !== original;
  const canSubmit = dirty && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      // 后端用空字符串清空 display_name；trimmed=='' 时也走这条路径
      const updated = await api.updateMyProfile({ display_name: trimmed });
      // 同步 authStore — sidebar 头像名等立即刷新
      if (token && user) {
        login(token, { ...user, display_name: updated.display_name ?? null });
      }
      // 通知其他持有 UserResponse 副本的组件刷新（UserManagementPanel 列表 +
      // UserDetailForm 详情）。authStore 不是这些组件的真相来源，必须显式 bump
      bumpListVersion();
      onClose();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message || '保存失败');
      } else {
        setError(err instanceof Error ? err.message : '保存失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!user) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onClick={submitting ? undefined : onClose}
    >
      <div
        className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-card shadow-modal max-w-sm w-full mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark mb-1">
          修改显示名
        </h2>
        <p className="text-text-secondary dark:text-text-secondary-dark mb-6 text-sm">
          显示名留空则恢复使用用户名 <span className="font-mono">@{user.username}</span>。
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-text-secondary dark:text-text-secondary-dark mb-1">
              显示名
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              autoFocus
              disabled={submitting}
              maxLength={128}
              placeholder={user.username}
              className="w-full px-3 py-2 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark focus:outline-none focus:border-accent disabled:opacity-40"
            />
          </div>

          {error && (
            <div className="text-status-error text-sm">{error}</div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-6 py-2 rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="px-6 py-2 rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
            >
              {submitting ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

'use client';

import { useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  INPUT_ON_SURFACE,
  LABEL_CLASS,
} from '@/lib/styles';
import DialogShell from './DialogShell';

interface EditDisplayNameDialogProps {
  onClose: () => void;
}

export default function EditDisplayNameDialog({ onClose }: EditDisplayNameDialogProps) {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const bumpListVersion = useUIStore((s) => s.bumpUserMgmtListVersion);

  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      // 用后端返回的完整 UserInfo 覆盖 store — 不能 spread 旧 user，否则
      // 期间被 admin 移过的 department_path 会被回写成过期值
      setUser(updated);
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
    <DialogShell
      title="修改显示名"
      description={
        <>
          显示名留空则恢复使用用户名 <span className="font-mono">@{user.username}</span>。
        </>
      }
      onClose={onClose}
      closeOnBackdrop={!submitting}
      closeOnEscape={!submitting}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className={LABEL_CLASS}>
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
            className={INPUT_ON_SURFACE}
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
            className={`${BUTTON_SECONDARY} rounded-lg px-6 py-2`}
          >
            取消
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
          >
            {submitting ? '保存中...' : '保存'}
          </button>
        </div>
      </form>
    </DialogShell>
  );
}

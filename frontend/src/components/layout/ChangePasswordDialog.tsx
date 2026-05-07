'use client';

import { useState } from 'react';
import * as api from '@/lib/api';

interface ChangePasswordDialogProps {
  onClose: () => void;
}

export default function ChangePasswordDialog({ onClose }: ChangePasswordDialogProps) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= 4 &&
    newPassword === confirmPassword &&
    !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      await api.changeMyPassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setSuccess(true);
      setTimeout(onClose, 1200);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (/\b400\b/.test(msg) && /current password/i.test(msg)) {
        setError('当前密码错误');
      } else if (/\b422\b/.test(msg)) {
        setError('新密码不符合要求（至少 4 个字符）');
      } else {
        setError(msg.replace(/^API \d+:\s*/, '') || '修改失败，请重试');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const newPasswordMismatch =
    confirmPassword.length > 0 && newPassword !== confirmPassword;

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
          修改密码
        </h2>
        <p className="text-text-secondary dark:text-text-secondary-dark mb-6 text-sm">
          修改后当前登录会话仍然有效，下次登录时使用新密码。
        </p>

        {success ? (
          <div className="py-4 text-center text-status-success">密码已更新</div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-text-secondary dark:text-text-secondary-dark mb-1">
                当前密码
              </label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                autoFocus
                disabled={submitting}
                className="w-full px-3 py-2 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark focus:outline-none focus:border-accent disabled:opacity-40"
              />
            </div>
            <div>
              <label className="block text-sm text-text-secondary dark:text-text-secondary-dark mb-1">
                新密码（至少 4 个字符）
              </label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                disabled={submitting}
                className="w-full px-3 py-2 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark focus:outline-none focus:border-accent disabled:opacity-40"
              />
            </div>
            <div>
              <label className="block text-sm text-text-secondary dark:text-text-secondary-dark mb-1">
                确认新密码
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                disabled={submitting}
                className="w-full px-3 py-2 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark focus:outline-none focus:border-accent disabled:opacity-40"
              />
              {newPasswordMismatch && (
                <p className="text-status-error text-xs mt-1">两次输入的密码不一致</p>
              )}
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
                {submitting ? '修改中...' : '确认修改'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

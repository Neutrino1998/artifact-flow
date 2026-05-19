'use client';

import { useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  INPUT_ON_PANEL,
  LABEL_CLASS,
} from '@/lib/styles';
import DialogShell from './DialogShell';

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
      // pwd_v bumped server-side → current token is invalid; explicitly log out
      // so AuthGuard redirects to /login instead of waiting for next 401.
      setTimeout(() => useAuthStore.getState().logout(), 1500);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 400) {
          setError('当前密码错误');
        } else if (err.status === 422) {
          setError('新密码不符合要求（至少 4 个字符）');
        } else {
          setError(err.message || '修改失败，请重试');
        }
      } else {
        setError(err instanceof Error ? err.message : '修改失败，请重试');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const newPasswordMismatch =
    confirmPassword.length > 0 && newPassword !== confirmPassword;

  return (
    <DialogShell
      title="修改密码"
      description="修改后所有已登录的设备（包括当前页）都会被强制重新登录。"
      onClose={onClose}
      closeOnBackdrop={!submitting}
      closeOnEscape={!submitting}
      surfaceClassName="bg-chat dark:bg-chat-dark"
    >
      {success ? (
        <div className="py-4 text-center text-status-success">
          密码已更新，即将退出登录...
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className={LABEL_CLASS}>
              当前密码
            </label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoFocus
              disabled={submitting}
              className={INPUT_ON_PANEL}
            />
          </div>
          <div>
            <label className={LABEL_CLASS}>
              新密码（至少 4 个字符）
            </label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={submitting}
              className={INPUT_ON_PANEL}
            />
          </div>
          <div>
            <label className={LABEL_CLASS}>
              确认新密码
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={submitting}
              className={INPUT_ON_PANEL}
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
              className={`${BUTTON_SECONDARY} rounded-lg px-6 py-2`}
            >
              取消
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
            >
              {submitting ? '修改中...' : '确认修改'}
            </button>
          </div>
        </form>
      )}
    </DialogShell>
  );
}

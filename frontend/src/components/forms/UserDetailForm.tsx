'use client';

import { useCallback, useEffect, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';
import type { UserResponse } from '@/types';
import DangerConfirmModal from '@/components/layout/DangerConfirmModal';

interface UserDetailFormProps {
  userId: string;
}

const ROLE_OPTIONS = [
  { value: 'user', label: 'user' },
  { value: 'admin', label: 'admin' },
];

export default function UserDetailForm({ userId }: UserDetailFormProps) {
  const currentUserId = useAuthStore((s) => s.user?.id);
  const setRightView = useUIStore((s) => s.setUserManagementRightView);
  const bumpListVersion = useUIStore((s) => s.bumpUserMgmtListVersion);

  const isSelf = currentUserId === userId;

  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Editable fields (mirror server state, edited locally then submitted)
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<'user' | 'admin'>('user');
  const [isActive, setIsActive] = useState(true);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // 重置密码：留空则不修改，与其他字段一起走主保存
  const [newPassword, setNewPassword] = useState('');

  // Delete confirmation
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteImpact, setDeleteImpact] = useState<number | null>(null);
  const [deleteImpactError, setDeleteImpactError] = useState<string | null>(null);

  const loadUser = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const u = await api.getUser(userId);
      setUser(u);
      setDisplayName(u.display_name ?? '');
      setRole(u.role === 'admin' ? 'admin' : 'user');
      setIsActive(u.is_active);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : '加载用户失败');
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadUser();
    // Reset sub-state when switching user
    setNewPassword('');
    setSaveError(null);
    setConfirmDelete(false);
    setDeleteImpact(null);
    setDeleteImpactError(null);
  }, [loadUser]);

  // 非空 < 4 字符视作无效；空值表示不修改密码
  const passwordInvalid = newPassword.length > 0 && newPassword.length < 4;
  const passwordChanged = newPassword.length > 0;

  const dirty =
    user !== null &&
    (
      (displayName.trim() || null) !== (user.display_name ?? null) ||
      role !== user.role ||
      isActive !== user.is_active ||
      passwordChanged
    );

  const handleSave = async () => {
    if (!user || !dirty || saving || passwordInvalid) return;
    setSaving(true);
    setSaveError(null);
    try {
      const patch: Record<string, unknown> = {};
      const trimmedDisplay = displayName.trim() || null;
      if (trimmedDisplay !== (user.display_name ?? null)) {
        patch.display_name = trimmedDisplay;
      }
      if (role !== user.role) patch.role = role;
      if (isActive !== user.is_active) patch.is_active = isActive;
      if (passwordChanged) patch.password = newPassword;

      const updated = await api.updateUser(user.id, patch);
      setUser(updated);
      // 保存成功后清空密码，避免下次再点"保存"重复改密
      if (passwordChanged) setNewPassword('');

      // Sync authStore if editing self's display_name
      if (isSelf && 'display_name' in patch) {
        const auth = useAuthStore.getState();
        if (auth.user && auth.token) {
          auth.login(auth.token, { ...auth.user, display_name: updated.display_name ?? null });
        }
      }
      bumpListVersion();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const openDeleteConfirm = async () => {
    if (!user) return;
    setDeleteImpactError(null);
    try {
      const impact = await api.getUserImpact(user.id);
      setDeleteImpact(impact.conversation_count);
      setConfirmDelete(true);
    } catch (err) {
      setDeleteImpactError(err instanceof ApiError ? err.message : '加载影响数据失败');
    }
  };

  const handleDelete = async () => {
    if (!user) return;
    await api.deleteUser(user.id);
    bumpListVersion();
    setConfirmDelete(false);
    setRightView({ type: 'empty' });
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-chat dark:bg-chat-dark p-6">
        <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark">加载中...</div>
      </div>
    );
  }

  if (loadError || !user) {
    return (
      <div className="flex-1 flex flex-col gap-3 items-center justify-center bg-chat dark:bg-chat-dark p-6">
        <div className="text-sm text-status-error">{loadError ?? '用户不存在'}</div>
        <button
          onClick={loadUser}
          className="px-4 py-1.5 rounded-lg border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
        >
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      {/* Header */}
      <div className="px-6 pt-5 pb-3 border-b border-border dark:border-border-dark">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-base font-semibold text-text-primary dark:text-text-primary-dark truncate">
              {user.display_name || user.username}
            </div>
            <div className="text-xs font-mono text-text-tertiary dark:text-text-tertiary-dark truncate">
              @{user.username}
            </div>
          </div>
          <button
            onClick={() => setRightView({ type: 'empty' })}
            className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
            aria-label="关闭"
            title="关闭"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        {/* Read-only meta */}
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <div className="text-text-tertiary dark:text-text-tertiary-dark">用户 ID</div>
            <div className="font-mono break-all text-text-secondary dark:text-text-secondary-dark">{user.id}</div>
          </div>
          <div>
            <div className="text-text-tertiary dark:text-text-tertiary-dark">创建时间</div>
            <div className="text-text-secondary dark:text-text-secondary-dark">
              {new Date(user.created_at).toLocaleString()}
            </div>
          </div>
        </div>

        {/* Editable fields */}
        <div>
          <label className="block text-sm text-text-secondary dark:text-text-secondary-dark mb-1">
            显示名
          </label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={user.username}
            disabled={saving || isSelf}
            className="w-full px-3 py-2 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark focus:outline-none focus:border-accent disabled:opacity-40"
          />
        </div>

        <div>
          <label className="block text-sm text-text-secondary dark:text-text-secondary-dark mb-1">
            角色
          </label>
          <div className="relative">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as 'user' | 'admin')}
              disabled={saving || isSelf}
              className="w-full appearance-none px-3 py-2 pr-9 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark focus:outline-none focus:border-accent disabled:opacity-40"
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
          <label className="flex items-center gap-3 select-none">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              disabled={saving || isSelf}
              className="w-4 h-4 accent-accent disabled:cursor-not-allowed"
            />
            <span className="text-sm text-text-primary dark:text-text-primary-dark">
              启用账号
            </span>
          </label>
        </div>

        {/* Reset password — 自己看自己时整段隐藏（走 /me/password） */}
        {!isSelf && (
          <div>
            <label className="block text-sm text-text-secondary dark:text-text-secondary-dark mb-1">
              重置密码
              <span className="ml-2 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                （留空则不修改）
              </span>
            </label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={saving}
              placeholder="新密码至少 4 个字符"
              autoComplete="new-password"
              className="w-full px-3 py-2 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark focus:outline-none focus:border-accent disabled:opacity-40"
            />
            {passwordInvalid && (
              <p className="text-status-error text-xs mt-1">密码至少需要 4 个字符</p>
            )}
          </div>
        )}

        {saveError && (
          <div className="text-status-error text-sm">{saveError}</div>
        )}
        {deleteImpactError && (
          <div className="text-status-error text-sm">{deleteImpactError}</div>
        )}
      </div>

      {/* Footer — isSelf 时按钮位置替换为居中提示，沿用同一根分割线保持视觉一致 */}
      <div className="border-t border-border dark:border-border-dark px-6 py-4">
        {isSelf ? (
          <p className="text-center text-sm text-text-secondary dark:text-text-secondary-dark">
            查看自己的信息为只读。修改密码请使用左下角用户菜单。
          </p>
        ) : (
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={openDeleteConfirm}
              disabled={saving}
              title="硬删除该用户（级联删除其所有会话）"
              className="px-5 py-2 rounded-lg border border-status-error text-status-error hover:bg-status-error/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              删除用户
            </button>
            <button
              onClick={handleSave}
              disabled={!dirty || saving || passwordInvalid}
              className="px-6 py-2 rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
            >
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        )}
      </div>

      {confirmDelete && (
        <DangerConfirmModal
          title="删除用户"
          message={
            `用户：${user.display_name || user.username} (@${user.username})\n` +
            `将级联删除该用户的 ${deleteImpact ?? 0} 条会话及相关消息、事件、artifact。\n` +
            `操作不可恢复。`
          }
          confirmLabel="确认删除"
          onCancel={() => setConfirmDelete(false)}
          onConfirm={handleDelete}
        />
      )}
    </div>
  );
}

'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '@/lib/api';
import type { UserResponse } from '@/types';
import { useAuthStore } from '@/stores/authStore';

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function UserManagementModal({ open, onClose }: Props) {
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    username: '',
    password: '',
    display_name: '',
    role: 'user',
  });
  const [creating, setCreating] = useState(false);

  // Inline display_name editing
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const editRef = useRef<HTMLInputElement>(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listUsers();
      setUsers(res.users);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载用户列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchUsers();
      setShowCreate(false);
    }
  }, [open, fetchUsers]);

  const handleCreate = async () => {
    if (!createForm.username || !createForm.password) return;
    setCreating(true);
    setError(null);
    try {
      await api.createUser({
        username: createForm.username,
        password: createForm.password,
        display_name: createForm.display_name || undefined,
        role: createForm.role,
      });
      setCreateForm({ username: '', password: '', display_name: '', role: 'user' });
      setShowCreate(false);
      fetchUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建用户失败');
    } finally {
      setCreating(false);
    }
  };

  const handleToggleActive = async (user: UserResponse) => {
    try {
      await api.updateUser(user.id, { is_active: !user.is_active });
      fetchUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新用户状态失败');
    }
  };

  const startEditing = (user: UserResponse) => {
    setEditingId(user.id);
    setEditValue(user.display_name || '');
    // Focus after render
    setTimeout(() => editRef.current?.focus(), 0);
  };

  const saveDisplayName = async (user: UserResponse) => {
    setEditingId(null);
    const trimmed = editValue.trim();
    const newValue = trimmed || null;
    if (newValue === (user.display_name || null)) return;
    try {
      // Send empty string to clear, non-empty to set (backend: `is not None` check)
      await api.updateUser(user.id, { display_name: trimmed });
      // If editing self, update authStore
      const currentUser = useAuthStore.getState().user;
      if (currentUser && currentUser.id === user.id) {
        useAuthStore.getState().login(
          useAuthStore.getState().token!,
          { ...currentUser, display_name: newValue },
        );
      }
      fetchUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新显示名称失败');
    }
  };

  const isSelf = (user: UserResponse) =>
    useAuthStore.getState().user?.id === user.id;

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-card shadow-modal max-w-lg w-full mx-4 p-6 max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark">
            用户管理
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-3 px-3 py-2 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg">
            {error}
          </div>
        )}

        {/* Create user toggle */}
        {!showCreate ? (
          <button
            onClick={() => setShowCreate(true)}
            className="mb-4 flex items-center gap-2 px-3 py-2 text-sm text-accent hover:text-accent-hover transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M7 2v10M2 7h10" />
            </svg>
            新建用户
          </button>
        ) : (
          <div className="mb-4 p-3 bg-bg dark:bg-bg-dark border border-border dark:border-border-dark rounded-lg space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <input
                type="text"
                placeholder="用户名"
                value={createForm.username}
                onChange={(e) => setCreateForm((f) => ({ ...f, username: e.target.value }))}
                className="px-2 py-1.5 text-sm rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark placeholder:text-text-secondary/50"
              />
              <input
                type="password"
                placeholder="密码"
                value={createForm.password}
                onChange={(e) => setCreateForm((f) => ({ ...f, password: e.target.value }))}
                className="px-2 py-1.5 text-sm rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark placeholder:text-text-secondary/50"
              />
              <input
                type="text"
                placeholder="显示名（可选）"
                value={createForm.display_name}
                onChange={(e) => setCreateForm((f) => ({ ...f, display_name: e.target.value }))}
                className="px-2 py-1.5 text-sm rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark placeholder:text-text-secondary/50"
              />
              <select
                value={createForm.role}
                onChange={(e) => setCreateForm((f) => ({ ...f, role: e.target.value }))}
                className="px-2 py-1.5 text-sm rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark"
              >
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowCreate(false)}
                className="px-3 py-1.5 text-sm rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-surface dark:hover:bg-surface-dark transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleCreate}
                disabled={creating || !createForm.username || !createForm.password}
                className="px-3 py-1.5 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
              >
                {creating ? '创建中...' : '创建'}
              </button>
            </div>
          </div>
        )}

        {/* User list */}
        <div className="flex-1 overflow-y-auto -mx-6 px-6">
          {loading ? (
            <div className="text-sm text-text-secondary dark:text-text-secondary-dark text-center py-8">
              加载中...
            </div>
          ) : users.length === 0 ? (
            <div className="text-sm text-text-secondary dark:text-text-secondary-dark text-center py-8">
              暂无用户
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-text-secondary dark:text-text-secondary-dark border-b border-border dark:border-border-dark">
                  <th className="pb-2 font-medium">用户名</th>
                  <th className="pb-2 font-medium">角色</th>
                  <th className="pb-2 font-medium">状态</th>
                  <th className="pb-2 font-medium text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr
                    key={user.id}
                    className="border-b border-border/50 dark:border-border-dark/50 last:border-0"
                  >
                    <td className="py-2.5 text-text-primary dark:text-text-primary-dark">
                      {editingId === user.id ? (
                        <input
                          ref={editRef}
                          type="text"
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onBlur={() => saveDisplayName(user)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') saveDisplayName(user);
                            if (e.key === 'Escape') setEditingId(null);
                          }}
                          placeholder={user.username}
                          className="w-full px-1.5 py-0.5 text-sm rounded border border-accent bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark outline-none"
                        />
                      ) : (
                        <div
                          className="cursor-pointer group/name"
                          onClick={() => startEditing(user)}
                          title="点击编辑显示名称"
                        >
                          <div className="group-hover/name:text-accent transition-colors">
                            {user.display_name || user.username}
                          </div>
                          <div className="text-xs text-text-secondary dark:text-text-secondary-dark">
                            @{user.username}
                          </div>
                        </div>
                      )}
                    </td>
                    <td className="py-2.5">
                      <span
                        className={`inline-block px-1.5 py-0.5 text-xs rounded ${
                          user.role === 'admin'
                            ? 'bg-accent/10 text-accent'
                            : 'bg-bg dark:bg-bg-dark text-text-secondary dark:text-text-secondary-dark'
                        }`}
                      >
                        {user.role}
                      </span>
                    </td>
                    <td className="py-2.5">
                      <span
                        className={`inline-block w-2 h-2 rounded-full ${
                          user.is_active ? 'bg-green-500' : 'bg-red-400'
                        }`}
                        title={user.is_active ? '已启用' : '已禁用'}
                      />
                    </td>
                    <td className="py-2.5 text-right">
                      {isSelf(user) ? (
                        <span className="text-xs text-text-secondary dark:text-text-secondary-dark">
                          当前用户
                        </span>
                      ) : (
                        <button
                          onClick={() => handleToggleActive(user)}
                          className={`px-2 py-1 text-xs rounded border transition-colors ${
                            user.is_active
                              ? 'border-red-300 dark:border-red-700 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20'
                              : 'border-green-300 dark:border-green-700 text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20'
                          }`}
                        >
                          {user.is_active ? '禁用' : '启用'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

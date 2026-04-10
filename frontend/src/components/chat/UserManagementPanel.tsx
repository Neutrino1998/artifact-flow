'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '@/lib/api';
import type { UserResponse } from '@/types';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';

const PAGE_SIZE = 20;

export default function UserManagementPanel() {
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const queryRef = useRef(query);

  const currentUserId = useAuthStore((s) => s.user?.id);
  const setUserManagementVisible = useUIStore((s) => s.setUserManagementVisible);

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    username: '',
    password: '',
    display_name: '',
    role: 'user',
  });
  const [creating, setCreating] = useState(false);

  const fetchUsers = useCallback(async (searchQuery: string, offset = 0, append = false) => {
    setLoading(true);
    setError(null);
    try {
      const trimmed = searchQuery.trim() || undefined;
      const res = await api.listUsers(PAGE_SIZE, offset, trimmed);
      if (append) {
        setUsers((prev) => [...prev, ...res.users]);
      } else {
        setUsers(res.users);
      }
      setTotal(res.total);
      setHasMore(offset + res.users.length < res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载用户列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers('');
  }, [fetchUsers]);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    queryRef.current = value;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchUsers(value);
    }, 300);
  }, [fetchUsers]);

  const handleLoadMore = useCallback(() => {
    if (loading || !hasMore) return;
    fetchUsers(query, users.length, true);
  }, [loading, hasMore, query, users.length, fetchUsers]);

  const handleClose = useCallback(() => {
    setUserManagementVisible(false);
  }, [setUserManagementVisible]);

  const handleCreate = async () => {
    if (!createForm.username || !createForm.password) return;
    setCreating(true);
    setError(null);
    try {
      await api.createUser({
        username: createForm.username,
        password: createForm.password,
        display_name: createForm.display_name || null,
        role: createForm.role,
      });
      setCreateForm({ username: '', password: '', display_name: '', role: 'user' });
      setShowCreate(false);
      fetchUsers(query);
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建用户失败');
    } finally {
      setCreating(false);
    }
  };

  const handleUpdate = useCallback(async (userId: string, fields: Record<string, unknown>) => {
    try {
      await api.updateUser(userId, fields);
      // If editing self's display_name, sync authStore
      if (currentUserId === userId && 'display_name' in fields) {
        const { user, token, login } = useAuthStore.getState();
        if (user && token) {
          const displayName = (fields.display_name as string)?.trim() || null;
          login(token, { ...user, display_name: displayName });
        }
      }
      fetchUsers(queryRef.current);
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新用户失败');
      throw err;
    }
  }, [currentUserId, fetchUsers]);

  const handleToggleActive = useCallback(async (user: UserResponse) => {
    try {
      await api.updateUser(user.id, { is_active: !user.is_active });
      fetchUsers(queryRef.current);
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新用户状态失败');
    }
  }, [fetchUsers]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      {/* Search */}
      <div className="px-4 pt-4 pb-2">
        <div className="max-w-3xl mx-auto">
          <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-2xl shadow-float px-4 py-3 flex items-center gap-3">
            <svg
              className="flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark"
              width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
            >
              <circle cx="7" cy="7" r="5" />
              <path d="M11 11l3.5 3.5" />
            </svg>
            <input
              type="text"
              value={query}
              onChange={(e) => handleQueryChange(e.target.value)}
              placeholder="搜索用户名或显示名..."
              autoFocus
              className="flex-1 bg-transparent text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
            />
            <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              {total} 用户
            </span>
            <button
              onClick={handleClose}
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
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {/* Error */}
          {error && (
            <div className="mb-3 px-3 py-2 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg">
              {error}
            </div>
          )}

          {/* Create user toggle */}
          {!showCreate ? (
            <button
              onClick={() => setShowCreate(true)}
              className="w-full mb-3 flex items-center gap-2 px-4 py-2.5 text-accent bg-chat dark:bg-chat-dark rounded-2xl border border-border dark:border-border-dark hover:bg-panel dark:hover:bg-panel-accent-dark transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M7 2v10M2 7h10" />
              </svg>
              新建用户
            </button>
          ) : (
            <div className="mb-3 p-4 bg-chat dark:bg-chat-dark rounded-2xl border border-border dark:border-border-dark space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-lg shadow-float px-4 py-2.5">
                  <input
                    type="text"
                    placeholder="用户名"
                    value={createForm.username}
                    onChange={(e) => setCreateForm((f) => ({ ...f, username: e.target.value }))}
                    className="w-full bg-transparent text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
                  />
                </div>
                <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-lg shadow-float px-4 py-2.5">
                  <input
                    type="password"
                    placeholder="密码"
                    value={createForm.password}
                    onChange={(e) => setCreateForm((f) => ({ ...f, password: e.target.value }))}
                    className="w-full bg-transparent text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
                  />
                </div>
                <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-lg shadow-float px-4 py-2.5">
                  <input
                    type="text"
                    placeholder="显示名（可选）"
                    value={createForm.display_name}
                    onChange={(e) => setCreateForm((f) => ({ ...f, display_name: e.target.value }))}
                    className="w-full bg-transparent text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
                  />
                </div>
                <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-lg shadow-float px-4 py-2.5">
                  <select
                    value={createForm.role}
                    onChange={(e) => setCreateForm((f) => ({ ...f, role: e.target.value }))}
                    className="w-full bg-transparent text-text-primary dark:text-text-primary-dark outline-none"
                  >
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </select>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setShowCreate(false)}
                  className="px-8 py-1.5 rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating || !createForm.username || !createForm.password}
                  className="px-8 py-1.5 rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
                >
                  {creating ? '创建中...' : '创建'}
                </button>
              </div>
            </div>
          )}

          {/* User list */}
          {loading && users.length === 0 ? (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              加载中...
            </div>
          ) : users.length === 0 ? (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              {query ? '没有找到匹配的用户' : '暂无用户'}
            </div>
          ) : (
            <>
              {users.map((user) => (
                <UserRow
                  key={user.id}
                  user={user}
                  isSelf={user.id === currentUserId}
                  onUpdate={handleUpdate}
                  onToggleActive={handleToggleActive}
                />
              ))}

              {loading && (
                <div className="py-4 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  Loading...
                </div>
              )}

              {hasMore && !loading && (
                <button
                  onClick={handleLoadMore}
                  className="w-full py-2.5 text-sm text-text-secondary dark:text-text-secondary-dark rounded-lg hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60 transition-colors"
                >
                  显示更多
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function UserRow({
  user,
  isSelf,
  onUpdate,
  onToggleActive,
}: {
  user: UserResponse;
  isSelf: boolean;
  onUpdate: (userId: string, fields: Record<string, unknown>) => Promise<void>;
  onToggleActive: (user: UserResponse) => void;
}) {
  // Local editing state
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const editRef = useRef<HTMLInputElement>(null);
  const savingRef = useRef(false);

  // Local reset password state
  const [resettingPassword, setResettingPassword] = useState(false);
  const [passwordValue, setPasswordValue] = useState('');
  const [passwordSaving, setPasswordSaving] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  const startEditing = () => {
    setEditing(true);
    setEditValue(user.display_name || '');
    setTimeout(() => editRef.current?.focus(), 0);
  };

  const saveDisplayName = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    setEditing(false);
    try {
      const trimmed = editValue.trim();
      const newValue = trimmed || null;
      if (newValue !== (user.display_name || null)) {
        await onUpdate(user.id, { display_name: trimmed });
      }
    } catch {
      // error handled by parent
    } finally {
      savingRef.current = false;
    }
  };

  const cancelEditing = () => {
    savingRef.current = true;
    setEditing(false);
    queueMicrotask(() => { savingRef.current = false; });
  };

  const startResetPassword = () => {
    setResettingPassword(true);
    setPasswordValue('');
    setTimeout(() => passwordRef.current?.focus(), 0);
  };

  const handleResetPassword = async () => {
    if (!passwordValue.trim() || passwordValue.length < 4) return;
    setPasswordSaving(true);
    try {
      await onUpdate(user.id, { password: passwordValue });
      setResettingPassword(false);
      setPasswordValue('');
    } catch {
      // error handled by parent
    } finally {
      setPasswordSaving(false);
    }
  };

  return (
    <div className="flex items-center gap-4 px-4 py-3 rounded-lg hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60 transition-colors mb-1">
      {/* User info */}
      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            ref={editRef}
            type="text"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={saveDisplayName}
            onKeyDown={(e) => {
              if (e.key === 'Enter') saveDisplayName();
              if (e.key === 'Escape') cancelEditing();
            }}
            placeholder={user.username}
            className="w-full px-2 py-0.5 rounded border border-accent bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark outline-none"
          />
        ) : (
          <div
            className="cursor-pointer group/name"
            onClick={startEditing}
            title="点击编辑显示名称"
          >
            <div className="font-medium text-text-primary dark:text-text-primary-dark group-hover/name:text-accent transition-colors truncate">
              {user.display_name || user.username}
            </div>
            <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
              @{user.username}
              <span className="ml-2 opacity-60">{user.id}</span>
            </div>
          </div>
        )}

        {/* Reset password inline */}
        {resettingPassword && (
          <div className="flex items-center gap-1.5 mt-2">
            <input
              ref={passwordRef}
              type="password"
              value={passwordValue}
              onChange={(e) => setPasswordValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleResetPassword();
                if (e.key === 'Escape') setResettingPassword(false);
              }}
              placeholder="新密码（≥4位）"
              className="w-36 px-2 py-1 text-xs rounded border border-accent bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark outline-none placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark"
            />
            <button
              onClick={handleResetPassword}
              disabled={passwordSaving || passwordValue.length < 4}
              className="px-2 py-1 text-xs rounded bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
            >
              {passwordSaving ? '...' : '确认'}
            </button>
            <button
              onClick={() => setResettingPassword(false)}
              className="px-2 py-1 text-xs rounded border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              取消
            </button>
          </div>
        )}
      </div>

      {/* Role badge */}
      <span
        className={`flex-shrink-0 inline-block px-1.5 py-0.5 text-xs rounded ${
          user.role === 'admin'
            ? 'bg-accent/10 text-accent'
            : 'bg-bg dark:bg-bg-dark text-text-secondary dark:text-text-secondary-dark'
        }`}
      >
        {user.role}
      </span>

      {/* Status */}
      <span className="flex-shrink-0 inline-flex items-center gap-1.5">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            user.is_active ? 'bg-green-500' : 'bg-red-400'
          }`}
        />
        <span className={`text-xs ${user.is_active ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'}`}>
          {user.is_active ? '启用' : '禁用'}
        </span>
      </span>

      {/* Actions */}
      <div className="flex-shrink-0 flex items-center gap-1.5">
        {isSelf ? (
          <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
            当前
          </span>
        ) : (
          <>
            <button
              onClick={startResetPassword}
              className="px-2 py-1 text-xs rounded border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:text-accent hover:border-accent transition-colors"
            >
              重置密码
            </button>
            <button
              onClick={() => onToggleActive(user)}
              className={`px-2 py-1 text-xs rounded border transition-colors ${
                user.is_active
                  ? 'border-red-300 dark:border-red-700 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20'
                  : 'border-green-300 dark:border-green-700 text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20'
              }`}
            >
              {user.is_active ? '禁用' : '启用'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

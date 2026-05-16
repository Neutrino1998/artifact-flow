'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '@/lib/api';
import type { UserResponse, DepartmentTreeNode } from '@/types';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import Checkbox from '@/components/forms/Checkbox';
import PanelSearchBar from './PanelSearchBar';

function flattenDeptNames(nodes: DepartmentTreeNode[], out: Map<string, string>): void {
  for (const n of nodes) {
    out.set(n.id, n.name);
    if (n.children?.length) flattenDeptNames(n.children, out);
  }
}

const PAGE_SIZE = 20;

export default function UserManagementPanel() {
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [deptNames, setDeptNames] = useState<Map<string, string>>(new Map());
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const queryRef = useRef(query);

  const currentUserId = useAuthStore((s) => s.user?.id);
  const setUserManagementVisible = useUIStore((s) => s.setUserManagementVisible);
  const setRightView = useUIStore((s) => s.setUserManagementRightView);
  const rightView = useUIStore((s) => s.userManagementRightView);
  const listVersion = useUIStore((s) => s.userMgmtListVersion);
  const selectionMode = useUIStore((s) => s.selectionMode);
  const selection = useUIStore((s) => s.userManagementSelection);
  const enterSelectionMode = useUIStore((s) => s.enterSelectionMode);
  const exitSelectionMode = useUIStore((s) => s.exitSelectionMode);
  const toggleUserSelection = useUIStore((s) => s.toggleUserSelection);
  const setUserManagementSelection = useUIStore((s) => s.setUserManagementSelection);
  const claim = useLatestOnly();

  const fetchUsers = useCallback(async (searchQuery: string, offset = 0, append = false) => {
    // Initial load / debounced search / load-more / listVersion-bump all
    // funnel through here. Latest-only drops slow older queries (and stale
    // load-more pages) that would otherwise overwrite a newer result set.
    const isLatest = claim();
    setLoading(true);
    setError(null);
    try {
      const trimmed = searchQuery.trim() || undefined;
      const res = await api.listUsers(PAGE_SIZE, offset, trimmed);
      if (!isLatest()) return;
      if (append) {
        setUsers((prev) => [...prev, ...res.users]);
      } else {
        setUsers(res.users);
      }
      setTotal(res.total);
      setHasMore(offset + res.users.length < res.total);
    } catch (err) {
      if (!isLatest()) return;
      setError(err instanceof Error ? err.message : '加载用户列表失败');
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    fetchUsers('');
  }, [fetchUsers]);

  // 右面板表单成功后 bumpUserMgmtListVersion → 触发列表刷新（保留搜索词）
  useEffect(() => {
    if (listVersion === 0) return;
    fetchUsers(queryRef.current);
  }, [listVersion, fetchUsers]);

  // 拉部门树 — 给 UserRow 显示部门名用。dept 改名/搬家后 listVersion bump
  // 也会触发重拉（dept 管理面板内的写操作都会 bump）
  useEffect(() => {
    let cancelled = false;
    api.getDepartmentTree()
      .then((r) => {
        if (cancelled) return;
        const m = new Map<string, string>();
        flattenDeptNames(r.nodes, m);
        setDeptNames(m);
      })
      .catch(() => {
        // 静默：部门名只是辅助信息，加载失败不阻断用户列表
      });
    return () => { cancelled = true; };
  }, [listVersion]);

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

  // Esc 退出选择模式（与中间面板的其他 Esc 行为不打架 — 只在选择模式生效）
  useEffect(() => {
    if (!selectionMode) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') exitSelectionMode();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectionMode, exitSelectionMode]);

  const selectedSet = new Set(selection);
  const selectAllOnPage = useCallback(() => {
    const next = new Set(selection);
    for (const u of users) {
      if (u.id !== currentUserId) next.add(u.id);  // 自己不可选（self-protection）
    }
    setUserManagementSelection(Array.from(next));
  }, [selection, users, currentUserId, setUserManagementSelection]);
  const allOnPageSelected = users.length > 0
    && users.every((u) => u.id === currentUserId || selectedSet.has(u.id));

  const selectedUserId = rightView.type === 'edit-user' ? rightView.userId : null;

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={handleQueryChange}
        placeholder="搜索用户名 / 显示名 / 部门..."
        countLabel={`${total} 用户`}
        onClose={handleClose}
      />

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {/* Error */}
          {error && (
            <div className="mb-3 px-3 py-2 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg">
              {error}
            </div>
          )}

          {/* Top-level actions — selection mode shows selection toolbar instead */}
          {!selectionMode ? (
            <div className="mb-3 flex items-center gap-2">
              <button
                onClick={() => setRightView({ type: 'create-user' })}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-2xl border transition-colors ${
                  rightView.type === 'create-user'
                    ? 'text-accent border-accent bg-panel dark:bg-panel-accent-dark'
                    : 'text-accent border-border dark:border-border-dark bg-chat dark:bg-chat-dark hover:bg-panel dark:hover:bg-panel-accent-dark'
                }`}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M7 2v10M2 7h10" />
                </svg>
                新建用户
              </button>
              <button
                onClick={() => setRightView({ type: 'bulk-import' })}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-2xl border transition-colors ${
                  rightView.type === 'bulk-import'
                    ? 'text-accent border-accent bg-panel dark:bg-panel-accent-dark'
                    : 'text-text-secondary dark:text-text-secondary-dark border-border dark:border-border-dark bg-chat dark:bg-chat-dark hover:bg-panel dark:hover:bg-panel-accent-dark'
                }`}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M7 2v8M3 8l4 4 4-4M2 13h10" />
                </svg>
                批量导入
              </button>
              <button
                onClick={() => setRightView({ type: 'dept-manager' })}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-2xl border transition-colors ${
                  rightView.type === 'dept-manager'
                    ? 'text-accent border-accent bg-panel dark:bg-panel-accent-dark'
                    : 'text-text-secondary dark:text-text-secondary-dark border-border dark:border-border-dark bg-chat dark:bg-chat-dark hover:bg-panel dark:hover:bg-panel-accent-dark'
                }`}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M2 3h10M2 7h10M2 11h6" />
                </svg>
                管理部门
              </button>
              <button
                onClick={enterSelectionMode}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-2xl border text-text-secondary dark:text-text-secondary-dark border-border dark:border-border-dark bg-chat dark:bg-chat-dark hover:bg-panel dark:hover:bg-panel-accent-dark transition-colors"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="2" y="2" width="10" height="10" rx="1.5" />
                  <path d="M5 7l1.5 1.5L9 6" />
                </svg>
                批量管理
              </button>
            </div>
          ) : (
            <div className="mb-3 flex items-center gap-2 px-4 py-2.5 rounded-2xl border border-accent/40 bg-accent/5 dark:bg-accent/10">
              <span className="text-sm text-text-secondary dark:text-text-secondary-dark">
                已选 <span className="text-text-primary dark:text-text-primary-dark font-medium">{selection.length}</span> 项
              </span>
              <button
                onClick={selectAllOnPage}
                disabled={allOnPageSelected || users.length === 0}
                className="px-3 py-1 text-xs rounded-md border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                全选当前页
              </button>
              <div className="flex-1" />
              <button
                onClick={exitSelectionMode}
                className="px-3 py-1 text-xs rounded-md border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
              >
                退出
              </button>
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
                  isSelected={user.id === selectedUserId}
                  deptName={user.department_id ? deptNames.get(user.department_id) ?? null : null}
                  selectionMode={selectionMode}
                  isChecked={selectedSet.has(user.id)}
                  onToggleSelect={() => toggleUserSelection(user.id)}
                  onOpenDetail={() => setRightView({ type: 'edit-user', userId: user.id })}
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
  isSelected,
  deptName,
  selectionMode,
  isChecked,
  onToggleSelect,
  onOpenDetail,
}: {
  user: UserResponse;
  isSelf: boolean;
  isSelected: boolean;
  deptName: string | null;
  selectionMode: boolean;
  isChecked: boolean;
  onToggleSelect: () => void;
  onOpenDetail: () => void;
}) {
  // 选择模式下：自己不可选（self-protection 在后端兜底，前端先打 affordance）；
  // 行点击切换选中而不是打开详情。
  const handleClick = () => {
    if (selectionMode) {
      if (!isSelf) onToggleSelect();
    } else {
      onOpenDetail();
    }
  };
  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleClick();
    }
  };

  const rowBg = selectionMode && isChecked
    ? 'bg-accent/10 dark:bg-accent/15'
    : isSelected
    ? 'bg-panel dark:bg-panel-accent-dark'
    : 'hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60';
  const rowCursor = selectionMode && isSelf ? 'cursor-not-allowed opacity-60' : 'cursor-pointer';

  return (
    <div
      role="button"
      tabIndex={selectionMode && isSelf ? -1 : 0}
      onClick={handleClick}
      onKeyDown={handleKey}
      title={selectionMode ? (isSelf ? '不能对自己执行批量操作' : '点击选中') : '点击查看详情'}
      className={`flex items-center gap-4 px-4 py-3 rounded-lg transition-colors mb-1 ${rowBg} ${rowCursor}`}
    >
      {selectionMode && (
        <Checkbox
          checked={isChecked}
          disabled={isSelf}
          onChange={() => { if (!isSelf) onToggleSelect(); }}
          onClick={(e) => e.stopPropagation()}
          ariaLabel={`选中 ${user.display_name || user.username}`}
        />
      )}
      {/* User info */}
      <div className="flex-1 min-w-0">
        <div className="font-medium text-text-primary dark:text-text-primary-dark truncate">
          {user.display_name || user.username}
        </div>
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
          @{user.username}
          {deptName && <span className="ml-2">{deptName}</span>}
          <span className="ml-2 opacity-60">{user.id}</span>
        </div>
      </div>

      {/* "当前" badge — placed before role/status so the eye lands on
          identity first, then runs through the right-aligned status cluster */}
      {isSelf && (
        <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          当前
        </span>
      )}

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
    </div>
  );
}

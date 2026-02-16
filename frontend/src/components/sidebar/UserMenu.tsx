'use client';

import { useState, useRef, useEffect } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';
import UserManagementModal from './UserManagementModal';

export default function UserMenu({ collapsed }: { collapsed?: boolean }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const toggleTheme = useUIStore((s) => s.toggleTheme);
  const theme = useUIStore((s) => s.theme);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const isAdmin = user?.role === 'admin';
  const initial = (user?.display_name || user?.username || '?')[0].toUpperCase();

  // Close popover on outside click
  useEffect(() => {
    if (!popoverOpen) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setPopoverOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [popoverOpen]);

  const handleLogout = () => {
    setPopoverOpen(false);
    logout();
    // AuthGuard handles the redirect to /login when isAuthenticated becomes false
  };

  const handleManageUsers = () => {
    setPopoverOpen(false);
    setModalOpen(true);
  };

  if (!user) return null;

  return (
    <>
      <div ref={containerRef} className="relative">
        {/* Trigger */}
        {collapsed ? (
          <button
            onClick={() => setPopoverOpen((o) => !o)}
            className="w-10 h-10 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            title={user.display_name || user.username}
          >
            <div className="w-7 h-7 rounded-full bg-accent/15 text-accent flex items-center justify-center text-xs font-medium">
              {initial}
            </div>
          </button>
        ) : (
          <button
            onClick={() => setPopoverOpen((o) => !o)}
            className="w-full flex items-center gap-3 px-4 py-3 border-t border-border dark:border-border-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors text-left"
          >
            <div className="w-8 h-8 rounded-full bg-accent/15 text-accent flex items-center justify-center text-sm font-medium shrink-0">
              {initial}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-text-primary dark:text-text-primary-dark truncate flex items-center gap-1.5">
                <span className="truncate">{user.display_name || user.username}</span>
                {isAdmin && (
                  <span className="inline-block px-1 py-px text-[10px] rounded bg-accent/10 text-accent shrink-0">
                    admin
                  </span>
                )}
              </div>
              <div className="text-xs text-text-secondary dark:text-text-secondary-dark truncate">
                @{user.username}
              </div>
            </div>
            <svg
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              className="text-text-secondary dark:text-text-secondary-dark shrink-0"
            >
              <path d="M3 5l3-3 3 3M3 7l3 3 3-3" />
            </svg>
          </button>
        )}

        {/* Popover (opens upward) */}
        {popoverOpen && (
          <div
            className={`absolute z-40 bottom-full mb-1 ${
              collapsed ? 'left-0' : 'left-2 right-2'
            } min-w-[180px] bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-card shadow-modal py-1`}
          >
            {/* Theme toggle */}
            <button
              onClick={() => {
                toggleTheme();
                setPopoverOpen(false);
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              {theme === 'light' ? (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M14 9.27A6 6 0 0 1 6.73 2 6 6 0 1 0 14 9.27z" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M8 1v1m0 12v1m7-7h-1M2 8H1m12.07-4.07-.71.71M3.64 12.36l-.71.71m10.14 0-.71-.71M3.64 3.64l-.71-.71M11 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" />
                </svg>
              )}
              {theme === 'light' ? '深色模式' : '浅色模式'}
            </button>

            {/* Admin: manage users */}
            {isAdmin && (
              <button
                onClick={handleManageUsers}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="8" cy="5" r="3" />
                  <path d="M2 14c0-3.3 2.7-6 6-6s6 2.7 6 6" />
                </svg>
                管理用户
              </button>
            )}

            {/* Divider */}
            <div className="my-1 border-t border-border dark:border-border-dark" />

            {/* Logout */}
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-500 hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M6 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3M11 11l3-3-3-3M6 8h8" />
              </svg>
              退出登录
            </button>
          </div>
        )}
      </div>

      {/* User management modal */}
      <UserManagementModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </>
  );
}

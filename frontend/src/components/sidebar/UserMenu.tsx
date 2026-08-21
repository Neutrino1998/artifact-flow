'use client';

import { useState, useRef, useEffect } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';
import ChangePasswordDialog from '@/components/layout/ChangePasswordDialog';
import EditDisplayNameDialog from '@/components/layout/EditDisplayNameDialog';
import PersonalAccessTokenDialog from '@/components/layout/PersonalAccessTokenDialog';
import { PillBadge } from '@/components/ui/PillBadge';
import { SwitchTrack } from '@/components/ui/SwitchTrack';
import { MENU_ROW_HOVER, MENU_ROW_DANGER_HOVER } from '@/lib/styles';
import {
  enableTaskNotifications,
  setTaskNotificationPreference,
  TASK_NOTIFICATION_PREFERENCE_EVENT,
  type TaskNotificationPreferenceDetail,
  taskNotificationsEnabled,
  taskNotificationsSupported,
} from '@/lib/taskNotifications';
import StorageBar from './StorageBar';

type TaskNotificationCapability = NotificationPermission | 'loading' | 'unsupported';

export default function UserMenu({
  collapsed,
  onNavigate = () => {},
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const toggleTheme = useUIStore((s) => s.toggleTheme);
  const theme = useUIStore((s) => s.theme);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);
  const [editProfileOpen, setEditProfileOpen] = useState(false);
  const [personalAccessTokenOpen, setPersonalAccessTokenOpen] = useState(false);
  const [taskNotificationsOn, setTaskNotificationsOn] = useState(false);
  const [taskNotificationCapability, setTaskNotificationCapability] =
    useState<TaskNotificationCapability>('loading');
  const setActiveMode = useUIStore((s) => s.setActiveMode);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});

  const isAdmin = user?.role === 'admin';
  const initial = (user?.display_name || user?.username || '?')[0].toUpperCase();
  // 显示用户所属部门（叶子部门名）— 后端通过 UserInfo.department_path 一并返回，
  // sidebar 不必再拉一次部门树。无部门 → 留空，回退到原 "@username"。
  const deptLeaf = user?.department_path?.length
    ? user.department_path[user.department_path.length - 1]
    : null;

  useEffect(() => {
    const userId = user?.id;
    if (!userId) {
      setTaskNotificationsOn(false);
      setTaskNotificationCapability('loading');
      return;
    }

    const sync = () => {
      if (!taskNotificationsSupported()) {
        setTaskNotificationsOn(false);
        setTaskNotificationCapability('unsupported');
        return;
      }
      setTaskNotificationsOn(taskNotificationsEnabled(userId));
      setTaskNotificationCapability(window.Notification.permission);
    };

    sync();
    const onPreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<TaskNotificationPreferenceDetail>).detail;
      if (!detail || detail.userId !== userId) {
        sync();
        return;
      }
      setTaskNotificationsOn(detail.enabled && detail.permission !== 'denied');
      if (detail.permission) setTaskNotificationCapability(detail.permission);
    };

    window.addEventListener('storage', sync);
    window.addEventListener('focus', sync);
    window.addEventListener(TASK_NOTIFICATION_PREFERENCE_EVENT, onPreferenceChange);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener('focus', sync);
      window.removeEventListener(TASK_NOTIFICATION_PREFERENCE_EVENT, onPreferenceChange);
    };
  }, [user?.id]);

  const togglePopover = () => {
    setPopoverOpen((prev) => {
      if (!prev && triggerRef.current) {
        const rect = triggerRef.current.getBoundingClientRect();
        setPopoverStyle({
          position: 'fixed',
          bottom: window.innerHeight - rect.top + 4,
          left: rect.left,
          minWidth: Math.max(collapsed ? 220 : rect.width, 220),
        });
      }
      return !prev;
    });
  };

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
    onNavigate();
    // AuthGuard handles the redirect to /login when isAuthenticated becomes false
  };

  const handleManageUsers = () => {
    setPopoverOpen(false);
    setActiveMode('userManagement');
    onNavigate();
  };

  const handleManageTools = () => {
    setPopoverOpen(false);
    setActiveMode('toolUnit');
    onNavigate();
  };

  const handleDepartmentAccess = () => {
    setPopoverOpen(false);
    setActiveMode('departmentAccess');
    onNavigate();
  };

  const handleObservability = () => {
    setPopoverOpen(false);
    setActiveMode('observability');
    onNavigate();
  };

  const handleInstances = () => {
    setPopoverOpen(false);
    setActiveMode('instances');
    onNavigate();
  };

  const handleNotificationConfig = () => {
    setPopoverOpen(false);
    setActiveMode('notificationConfig');
    onNavigate();
  };

  const handleChangePassword = () => {
    setPopoverOpen(false);
    setChangePasswordOpen(true);
  };

  const handleEditProfile = () => {
    setPopoverOpen(false);
    setEditProfileOpen(true);
  };

  const handlePersonalAccessTokens = () => {
    setPopoverOpen(false);
    setPersonalAccessTokenOpen(true);
  };

  const handleTaskNotificationsToggle = () => {
    if (!user) return;
    if (taskNotificationsOn) {
      setTaskNotificationPreference(user.id, false);
      return;
    }
    void enableTaskNotifications(user.id);
  };

  if (!user) return null;

  return (
    <>
      <div ref={containerRef} className="relative">
        {/* Trigger */}
        {collapsed ? (
          <button
            ref={triggerRef}
            onClick={togglePopover}
            className={`w-10 h-10 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark ${MENU_ROW_HOVER}`}
            title={user.display_name || user.username}
          >
            <div className="w-7 h-7 rounded-lg bg-panel-accent dark:bg-surface-dark text-text-primary dark:text-text-primary-dark ring-1 ring-border/60 dark:ring-border-dark/60 flex items-center justify-center text-xs font-medium">
              {initial}
            </div>
          </button>
        ) : (
          <button
            ref={triggerRef}
            onClick={togglePopover}
            className={`w-full flex items-center gap-3 px-3 py-2.5 bg-chat dark:bg-panel-accent-dark rounded-card text-left ${MENU_ROW_HOVER}`}
          >
            <div className="w-8 h-8 rounded-lg bg-panel-accent dark:bg-surface-dark text-text-primary dark:text-text-primary-dark ring-1 ring-border/60 dark:ring-border-dark/60 flex items-center justify-center font-medium shrink-0">
              {initial}
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-medium text-text-primary dark:text-text-primary-dark truncate flex items-center gap-1.5">
                <span className="truncate">{user.display_name || user.username}</span>
                {isAdmin && (
                  <PillBadge tone="accent">admin</PillBadge>
                )}
              </div>
              <div className="text-xs text-text-secondary dark:text-text-secondary-dark truncate">
                {deptLeaf && <span>{deptLeaf} </span>}
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
            className="z-40 max-h-[calc(100vh-24px)] overflow-y-auto bg-bg dark:bg-panel-accent-dark border-none rounded-card shadow-modal p-1.5"
            style={popoverStyle}
          >
            <div className="mb-1.5">
              <StorageBar />
            </div>

            {/* Theme toggle */}
            <button
              onClick={() => {
                toggleTheme();
                setPopoverOpen(false);
              }}
              className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
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

            {/* Browser-level task terminal notifications (opt-out by default). */}
            <div className="w-full flex items-center gap-2 px-2.5 py-2 text-text-primary dark:text-text-primary-dark rounded-lg">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="shrink-0">
                <path d="M8 2v1M4 6a4 4 0 0 1 8 0v3l1.5 2H2.5L4 9V6z" strokeLinejoin="round" />
                <path d="M6.5 13a1.5 1.5 0 0 0 3 0" />
              </svg>
              <div className="min-w-0 flex-1">
                <div className="font-medium">任务完成通知</div>
                {taskNotificationCapability === 'default' && taskNotificationsOn && (
                  <div className="text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
                    首次发送任务时询问权限
                  </div>
                )}
                {taskNotificationCapability === 'denied' && (
                  <div className="text-[11px] text-status-warning">
                    请在浏览器网站设置中允许
                  </div>
                )}
                {taskNotificationCapability === 'unsupported' && (
                  <div className="text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
                    当前浏览器不支持
                  </div>
                )}
              </div>
              <button
                type="button"
                role="switch"
                aria-label="任务完成通知"
                aria-checked={taskNotificationsOn}
                disabled={taskNotificationCapability === 'unsupported' || taskNotificationCapability === 'denied'}
                onClick={handleTaskNotificationsToggle}
                className="shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <SwitchTrack checked={taskNotificationsOn} />
              </button>
            </div>

            {user.can_edit_profile && (
              <button
                onClick={handleEditProfile}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M11 2l3 3-9 9H2v-3z" />
                </svg>
                修改显示名
              </button>
            )}

            {user.can_change_password && (
              <button
                onClick={handleChangePassword}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="3" y="7" width="10" height="7" rx="1" />
                  <path d="M5 7V5a3 3 0 0 1 6 0v2" />
                </svg>
                修改密码
              </button>
            )}

            <button
              onClick={handlePersonalAccessTokens}
              className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <path d="M10.5 0a5.499 5.499 0 1 1-1.288 10.848l-.932.932a.749.749 0 0 1-.53.22H7v.75a.749.749 0 0 1-.22.53l-.5.5a.749.749 0 0 1-.53.22H5v.75a.749.749 0 0 1-.22.53l-.5.5a.749.749 0 0 1-.53.22h-2A1.75 1.75 0 0 1 0 14.25v-2c0-.199.079-.389.22-.53l4.932-4.932A5.5 5.5 0 0 1 10.5 0Zm-4 5.5c-.001.431.069.86.205 1.269a.75.75 0 0 1-.181.768L1.5 12.56v1.69c0 .138.112.25.25.25h1.69l.06-.06v-1.19a.75.75 0 0 1 .75-.75h1.19l.06-.06v-1.19a.75.75 0 0 1 .75-.75h1.19l1.023-1.025a.75.75 0 0 1 .768-.18A4 4 0 1 0 6.5 5.5ZM11 6a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z" />
              </svg>
              API 密钥
            </button>

            {/* Admin: manage users */}
            {isAdmin && (
              <button
                onClick={handleManageUsers}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="8" cy="5" r="3" />
                  <path d="M2 14c0-3.3 2.7-6 6-6s6 2.7 6 6" />
                </svg>
                用户管理
              </button>
            )}

            {/* Admin: manage tool units */}
            {isAdmin && (
              <button
                onClick={handleManageTools}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9.8 4.2a.67.67 0 0 0 0 .93l1.07 1.07a.67.67 0 0 0 .93 0l2.51-2.51a4 4 0 0 1-5.29 5.29l-4.61 4.61a1.41 1.41 0 0 1-2-2l4.61-4.61a4 4 0 0 1 5.29-5.29l-2.51 2.51z" />
                </svg>
                工具管理
              </button>
            )}

            {/* Admin: department access */}
            {isAdmin && (
              <button
                onClick={handleDepartmentAccess}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 4h10M3 8h10M3 12h6" />
                  <path d="M11.5 11.5l1 1 1.8-2" />
                </svg>
                部门授权
              </button>
            )}

            {/* Admin: observability */}
            {isAdmin && (
              <button
                onClick={handleObservability}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 12a6 6 0 1 1 12 0" />
                  <path d="M8 12l3-4" />
                  <circle cx="8" cy="12" r="0.6" fill="currentColor" stroke="none" />
                </svg>
                会话监控
              </button>
            )}

            {/* Admin: fleet instances */}
            {isAdmin && (
              <button
                onClick={handleInstances}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="2.5" width="12" height="4" rx="1" />
                  <rect x="2" y="9.5" width="12" height="4" rx="1" />
                  <circle cx="4.5" cy="4.5" r="0.6" fill="currentColor" stroke="none" />
                  <circle cx="4.5" cy="11.5" r="0.6" fill="currentColor" stroke="none" />
                </svg>
                实例监控
              </button>
            )}

            {/* Admin: site notifications */}
            {isAdmin && (
              <button
                onClick={handleNotificationConfig}
                className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M8 2v1M4 6a4 4 0 0 1 8 0v3l1.5 2H2.5L4 9V6z" />
                  <path d="M6.5 13a1.5 1.5 0 0 0 3 0" />
                </svg>
                通知管理
              </button>
            )}

            <div className="my-1 border-t border-border dark:border-border-dark" />

            {/* Logout */}
            <button
              onClick={handleLogout}
              className={`w-full flex items-center gap-2 px-2.5 py-2 font-medium text-status-error rounded-lg ${MENU_ROW_DANGER_HOVER}`}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M6 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3M11 11l3-3-3-3M6 8h8" />
              </svg>
              退出登录
            </button>
          </div>
        )}
      </div>

      {changePasswordOpen && user.can_change_password && (
        <ChangePasswordDialog onClose={() => setChangePasswordOpen(false)} />
      )}

      {editProfileOpen && user.can_edit_profile && (
        <EditDisplayNameDialog onClose={() => setEditProfileOpen(false)} />
      )}

      {personalAccessTokenOpen && (
        <PersonalAccessTokenDialog onClose={() => setPersonalAccessTokenOpen(false)} />
      )}
    </>
  );
}

'use client';

import { useState, useCallback } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useAuthStore } from '@/stores/authStore';
import { useChat } from '@/hooks/useChat';
import ConversationList from './ConversationList';
import AdminConversationList from './AdminConversationList';
import UserMenu from './UserMenu';
import NotificationCenter from './NotificationCenter';
import BrandingFooter from '@/components/BrandingFooter';
import { APP_NAME, APP_TAGLINE } from '@/lib/branding';

function IconButton({
  onClick,
  label,
  children,
}: {
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="w-10 h-10 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-chat/60 dark:hover:bg-panel-accent-dark/60 transition-colors"
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

// Plain text-row nav buttons (no border/fill) — icon + label with a subtle hover highlight.
const navRowClass =
  'w-full flex items-center gap-2.5 px-2 py-1.5 font-medium text-text-primary dark:text-text-primary-dark hover:bg-chat/70 dark:hover:bg-panel-accent-dark/60 rounded-lg transition-colors';

const navRowDangerClass =
  'w-full flex items-center gap-2.5 px-2 py-1.5 font-medium text-red-500 hover:bg-red-50 dark:hover:bg-red-900/10 rounded-lg transition-colors';

const RefreshIcon = ({ size = 16, spinning = false }: { size?: number; spinning?: boolean }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    className={spinning ? 'animate-spin-once' : ''}
  >
    <path d="M2 8a6 6 0 0 1 10.5-4M14 8a6 6 0 0 1-10.5 4" />
    <path d="M12.5 1v3h-3M3.5 15v-3h3" />
  </svg>
);

export default function Sidebar() {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const setConversationBrowserVisible = useUIStore((s) => s.setConversationBrowserVisible);
  const userManagementVisible = useUIStore((s) => s.userManagementVisible);
  const setUserManagementVisible = useUIStore((s) => s.setUserManagementVisible);

  const observabilityVisible = useUIStore((s) => s.observabilityVisible);
  const setObservabilityVisible = useUIStore((s) => s.setObservabilityVisible);
  const setObservabilityBrowseVisible = useUIStore((s) => s.setObservabilityBrowseVisible);
  const triggerObservabilityRefresh = useUIStore((s) => s.triggerObservabilityRefresh);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const { startNewChat } = useChat();

  const handleNewChat = () => {
    startNewChat();
    setArtifactPanelVisible(false);
    setConversationBrowserVisible(false);
    setUserManagementVisible(false);
    setObservabilityVisible(false);
  };

  const [refreshSpinning, setRefreshSpinning] = useState(false);

  const handleRefresh = useCallback(() => {
    triggerObservabilityRefresh();
    setRefreshSpinning(true);
    setTimeout(() => setRefreshSpinning(false), 600);
  }, [triggerObservabilityRefresh]);

  const handleSearchChat = () => {
    setConversationBrowserVisible(true);
  };

  const handleSearchAdmin = () => {
    setObservabilityBrowseVisible(true);
  };

  const handleExitObservability = () => {
    setObservabilityVisible(false);
  };

  const handleExitUserMgmt = () => {
    setUserManagementVisible(false);
  };

  const inObservability = observabilityVisible && isAdmin;
  // While user-management owns the right panel (master-detail on desktop,
  // force-hidden on mobile), the artifact toggle would just flip a hidden
  // store flag that ThreeColumnLayout's forceArtifactVisible overrides —
  // the button looks broken and leaks state across exit. Hide it here.
  const inUserMgmt = userManagementVisible && isAdmin;

  // ── Collapsed: 48px icon bar ──
  if (sidebarCollapsed) {
    return (
      <div className="flex flex-col items-center h-full bg-panel-accent dark:bg-panel-dark py-3 gap-1 w-full">
        {/* Expand */}
        <IconButton onClick={toggleSidebar} label="展开侧栏">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="1.5" y="1.5" width="13" height="13" rx="2" />
            <path d="M6 1.5v13" />
          </svg>
        </IconButton>

        {inObservability ? (
          <>
            {/* Search admin */}
            <IconButton onClick={handleSearchAdmin} label="搜索对话">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="7" cy="7" r="5" />
                <path d="M11 11l3.5 3.5" />
              </svg>
            </IconButton>

            {/* Refresh */}
            <IconButton onClick={handleRefresh} label="刷新对话">
              <RefreshIcon spinning={refreshSpinning} />
            </IconButton>

            {/* Exit observability */}
            <IconButton onClick={handleExitObservability} label="退出监控">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </IconButton>
          </>
        ) : (
          <>
            {/* Artifacts — hidden while user-management owns the right panel */}
            {!inUserMgmt && (
              <IconButton onClick={toggleArtifactPanel} label="文件面板">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="2" y="2" width="12" height="12" rx="1.5" />
                  <path d="M5 6h6M5 8.5h4" />
                </svg>
              </IconButton>
            )}

            {/* Search conversations */}
            <IconButton onClick={handleSearchChat} label="搜索对话">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="7" cy="7" r="5" />
                <path d="M11 11l3.5 3.5" />
              </svg>
            </IconButton>

            {/* New chat */}
            <IconButton onClick={handleNewChat} label="新建对话">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M8 3v10M3 8h10" />
              </svg>
            </IconButton>

            {/* Exit user management */}
            {inUserMgmt && (
              <IconButton onClick={handleExitUserMgmt} label="退出用户管理">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
              </IconButton>
            )}
          </>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Notifications (auto-hides when empty) */}
        <NotificationCenter collapsed />

        {/* User menu */}
        <UserMenu collapsed />
      </div>
    );
  }

  // ── Expanded: full sidebar ──
  return (
    <div className="flex flex-col h-full bg-panel-accent dark:bg-panel-dark">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border dark:border-border-dark">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark">
            {inObservability ? '运行监控' : inUserMgmt ? '用户管理' : APP_NAME}
          </h1>
          {!inObservability && !inUserMgmt && (
            <p className="text-xs text-text-secondary dark:text-text-secondary-dark">
              {APP_TAGLINE}
            </p>
          )}
        </div>
        <IconButton onClick={toggleSidebar} label="收起侧栏">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="1.5" y="1.5" width="13" height="13" rx="2" />
            <path d="M6 1.5v13" />
          </svg>
        </IconButton>
      </div>

      {/* Action buttons */}
      <div className="px-3 pt-3 pb-3 space-y-0.5">
        {inObservability ? (
          <>
            <button
              onClick={handleSearchAdmin}
              className={navRowClass}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="7" cy="7" r="5" />
                <path d="M11 11l3.5 3.5" />
              </svg>
              搜索对话
            </button>
            <button
              onClick={handleRefresh}
              className={navRowClass}
            >
              <RefreshIcon size={16} spinning={refreshSpinning} />
              刷新对话
            </button>
            <button
              onClick={handleExitObservability}
              className={navRowDangerClass}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M9 3H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h5M7 8h6m0 0l-2-2m2 2l-2 2" />
              </svg>
              退出监控
            </button>
          </>
        ) : (
          <>
            {/* Artifacts — hidden while user-management owns the right panel */}
            {!inUserMgmt && (
              <button
                onClick={toggleArtifactPanel}
                className={navRowClass}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="2" y="2" width="12" height="12" rx="1.5" />
                  <path d="M5 6h6M5 8.5h4" />
                </svg>
                文件面板
              </button>
            )}
            <button
              onClick={handleSearchChat}
              className={navRowClass}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="7" cy="7" r="5" />
                <path d="M11 11l3.5 3.5" />
              </svg>
              搜索对话
            </button>
            <button
              onClick={handleNewChat}
              className={navRowClass}
            >
              <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M7 2v10M2 7h10" />
              </svg>
              新建对话
            </button>
            {inUserMgmt && (
              <button
                onClick={handleExitUserMgmt}
                className={navRowDangerClass}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M9 3H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h5M7 8h6m0 0l-2-2m2 2l-2 2" />
                </svg>
                退出用户管理
              </button>
            )}
          </>
        )}
      </div>

      {/* Conversation list */}
      <div className="px-5 pt-2 pb-1 text-xs font-semibold text-text-tertiary dark:text-text-tertiary-dark">
        对话列表
      </div>
      {inObservability ? <AdminConversationList /> : <ConversationList />}

      {/* Notifications + user menu at bottom */}
      <div className="px-3 pb-3 pt-2 space-y-2">
        <NotificationCenter />
        <UserMenu />
      </div>
      <BrandingFooter variant="sidebar" />
    </div>
  );
}

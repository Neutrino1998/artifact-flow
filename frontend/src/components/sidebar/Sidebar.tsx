'use client';

import { useState, useCallback } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useAuthStore } from '@/stores/authStore';
import { useChat } from '@/hooks/useChat';
import ConversationList from './ConversationList';
import AdminConversationList from './AdminConversationList';
import UserMenu from './UserMenu';

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
      className="w-10 h-10 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

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

  const inObservability = observabilityVisible && isAdmin;

  // ── Collapsed: 48px icon bar ──
  if (sidebarCollapsed) {
    return (
      <div className="flex flex-col items-center h-full bg-panel-accent dark:bg-panel-dark py-3 gap-1 w-12">
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
            {/* Artifacts */}
            <IconButton onClick={toggleArtifactPanel} label="文稿面板">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="2" width="12" height="12" rx="1.5" />
                <path d="M5 6h6M5 8.5h4" />
              </svg>
            </IconButton>

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
          </>
        )}

        {/* Spacer */}
        <div className="flex-1" />

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
            {inObservability ? '运行监控' : 'ArtifactFlow'}
          </h1>
          {!inObservability && (
            <p className="text-xs text-text-secondary dark:text-text-secondary-dark">
              多智能体任务工作台
            </p>
          )}
        </div>
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          aria-label="Collapse sidebar"
          title="收起侧栏"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="1.5" y="1.5" width="13" height="13" rx="2" />
            <path d="M6 1.5v13" />
          </svg>
        </button>
      </div>

      {/* Action buttons */}
      <div className="px-3 pt-3 pb-3 space-y-2">
        {inObservability ? (
          <>
            <button
              onClick={handleSearchAdmin}
              className="w-full flex items-center gap-2 px-3 py-2 font-medium text-text-primary bg-chat dark:bg-panel-accent-dark dark:text-text-primary-dark rounded-card border border-border dark:border-border-dark hover:bg-chat/70 dark:hover:bg-surface-dark transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="7" cy="7" r="5" />
                <path d="M11 11l3.5 3.5" />
              </svg>
              搜索对话
            </button>
            <button
              onClick={handleRefresh}
              className="w-full flex items-center gap-2 px-3 py-2 font-medium text-text-primary bg-chat dark:bg-panel-accent-dark dark:text-text-primary-dark rounded-card border border-border dark:border-border-dark hover:bg-chat/70 dark:hover:bg-surface-dark transition-colors"
            >
              <RefreshIcon size={14} spinning={refreshSpinning} />
              刷新对话
            </button>
            <button
              onClick={handleExitObservability}
              className="w-full flex items-center gap-2 px-3 py-2 font-medium text-red-500 bg-chat dark:bg-panel-accent-dark rounded-card border border-border dark:border-border-dark hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M9 3H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h5M7 8h6m0 0l-2-2m2 2l-2 2" />
              </svg>
              退出监控
            </button>
          </>
        ) : (
          <>
            <button
              onClick={toggleArtifactPanel}
              className="w-full flex items-center gap-2 px-3 py-2 font-medium text-text-primary bg-chat dark:bg-panel-accent-dark dark:text-text-primary-dark rounded-card border border-border dark:border-border-dark hover:bg-chat/70 dark:hover:bg-surface-dark transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="2" width="12" height="12" rx="1.5" />
                <path d="M5 6h6M5 8.5h4" />
              </svg>
              文稿面板
            </button>
            <button
              onClick={handleSearchChat}
              className="w-full flex items-center gap-2 px-3 py-2 font-medium text-text-primary bg-chat dark:bg-panel-accent-dark dark:text-text-primary-dark rounded-card border border-border dark:border-border-dark hover:bg-chat/70 dark:hover:bg-surface-dark transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="7" cy="7" r="5" />
                <path d="M11 11l3.5 3.5" />
              </svg>
              搜索对话
            </button>
            <button
              onClick={handleNewChat}
              className="w-full flex items-center gap-2 px-3 py-2 font-medium text-text-primary bg-chat dark:bg-panel-accent-dark dark:text-text-primary-dark rounded-card border border-border dark:border-border-dark hover:bg-chat/70 dark:hover:bg-surface-dark transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M7 2v10M2 7h10" />
              </svg>
              新建对话
            </button>
          </>
        )}
      </div>

      {/* Conversation list */}
      {inObservability ? <AdminConversationList /> : <ConversationList />}

      {/* User menu at bottom */}
      <div className="px-3 pb-3 pt-2">
        <UserMenu />
      </div>
    </div>
  );
}

'use client';

import { useState, useCallback } from 'react';
import { useUIStore, type UserMgmtRightView } from '@/stores/uiStore';
import { useAuthStore } from '@/stores/authStore';
import { useChat } from '@/hooks/useChat';
import ConversationList from './ConversationList';
import AdminConversationList from './AdminConversationList';
import UserMenu from './UserMenu';
import StorageBar from './StorageBar';
import NotificationCenter from './NotificationCenter';
import BrandingFooter from '@/components/BrandingFooter';
import { APP_NAME, APP_TAGLINE } from '@/lib/branding';
import { BUTTON_GHOST_ICON } from '@/lib/styles';

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
      className={`${BUTTON_GHOST_ICON} w-10 h-10 flex items-center justify-center`}
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
  'w-full flex items-center gap-2.5 px-2 py-1.5 font-medium text-status-error hover:bg-status-error/10 rounded-lg transition-colors';

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

  const activeMode = useUIStore((s) => s.activeMode);
  const setActiveMode = useUIStore((s) => s.setActiveMode);
  const setObservabilityBrowseVisible = useUIStore((s) => s.setObservabilityBrowseVisible);
  const triggerObservabilityRefresh = useUIStore((s) => s.triggerObservabilityRefresh);
  const triggerInstancesRefresh = useUIStore((s) => s.triggerInstancesRefresh);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const { startNewChat } = useChat();

  // Admin-takeover actions — hoisted out of the middle master-list panels
  // into the sidebar. They just re-target the right master-detail panel.
  const setUserManagementRightView = useUIStore((s) => s.setUserManagementRightView);
  const setToolUnitRightView = useUIStore((s) => s.setToolUnitRightView);
  const selectionMode = useUIStore((s) => s.selectionMode);
  const enterSelectionMode = useUIStore((s) => s.enterSelectionMode);
  const exitSelectionMode = useUIStore((s) => s.exitSelectionMode);

  const handleNewChat = () => {
    startNewChat();
    setArtifactPanelVisible(false);
    setActiveMode('none'); // 单一动作关掉任何接管面板(取代旧的 4 次 set*Visible(false))
  };

  const [refreshSpinning, setRefreshSpinning] = useState(false);

  const handleRefresh = useCallback(() => {
    triggerObservabilityRefresh();
    setRefreshSpinning(true);
    setTimeout(() => setRefreshSpinning(false), 600);
  }, [triggerObservabilityRefresh]);

  const handleRefreshInstances = useCallback(() => {
    triggerInstancesRefresh();
    setRefreshSpinning(true);
    setTimeout(() => setRefreshSpinning(false), 600);
  }, [triggerInstancesRefresh]);

  const handleSearchChat = () => {
    setActiveMode('conversationBrowser');
  };

  const handleManageSkills = () => {
    setActiveMode('skills');
  };

  const handleSearchAdmin = () => {
    setObservabilityBrowseVisible(true);
  };

  const handleExit = () => {
    setActiveMode('none');
  };

  // Opening a form leaves selection mode — the old middle-panel button row was
  // replaced wholesale while selecting, so form + selection could never coexist;
  // keep that single-active invariant now that the buttons persist in the sidebar.
  const openUserView = (view: UserMgmtRightView) => {
    if (selectionMode) exitSelectionMode();
    setUserManagementRightView(view);
  };
  const handleCreateUser = () => openUserView({ type: 'create-user' });
  const handleBulkImport = () => openUserView({ type: 'bulk-import' });
  const handleDeptManager = () => openUserView({ type: 'dept-manager' });
  // Toggle: 批量管理 enters selection mode; pressing it again (or the middle
  // toolbar's 退出 / Esc) leaves it — keeps the sidebar in sync with the panel.
  const handleToggleSelection = () =>
    (selectionMode ? exitSelectionMode() : enterSelectionMode());
  const handleCreateUnit = () => setToolUnitRightView({ type: 'create-unit' });

  const inObservability = activeMode === 'observability' && isAdmin;
  // While a master-detail mode owns the right panel (force-shown on desktop,
  // force-hidden on mobile), the artifact toggle would just flip a hidden
  // store flag that ThreeColumnLayout's forceArtifactVisible overrides —
  // the button looks broken and leaks state across exit. Hide it here.
  const inUserMgmt = activeMode === 'userManagement' && isAdmin;
  // Tool-unit management is the same master-detail shape as user-mgmt.
  const inToolUnitMgmt = activeMode === 'toolUnit' && isAdmin;
  // Skill management (C-3) — center takeover like conversationBrowser, all users.
  const inSkills = activeMode === 'skills';
  // Fleet instances (Phase C) — center takeover, admin-only, like observability
  // but without the conversation search/refresh actions.
  const inInstances = activeMode === 'instances' && isAdmin;

  // 「全接管」admin 模式:实例监控/工具管理/用户管理。这三个把中间/右面板整个接管,
  // 与对话无关 → 侧栏隐藏对话列表 + 文件面板/搜索对话/新建对话/技能管理,只留退出
  // (实例监控额外留一个刷新)。会话监控(observability)不算 —— 它本就是看对话的,
  // 保留 admin 对话列表 + 搜索/刷新。
  const inAdminTakeover = inUserMgmt || inToolUnitMgmt || inInstances;
  const takeoverExitLabel = inUserMgmt
    ? '退出用户管理'
    : inToolUnitMgmt
      ? '退出工具管理'
      : '退出实例监控';

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
            <IconButton onClick={handleExit} label="退出监控">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </IconButton>
          </>
        ) : inAdminTakeover ? (
          <>
            {/* User-management actions */}
            {inUserMgmt && (
              <>
                <IconButton onClick={handleCreateUser} label="新建用户">
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M7 2v10M2 7h10" />
                  </svg>
                </IconButton>
                <IconButton onClick={handleBulkImport} label="批量导入">
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M7 2v8M3 8l4 4 4-4M2 13h10" />
                  </svg>
                </IconButton>
                <IconButton onClick={handleDeptManager} label="管理部门">
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M2 3h10M2 7h10M2 11h6" />
                  </svg>
                </IconButton>
                <IconButton onClick={handleToggleSelection} label="批量管理">
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <rect x="2" y="2" width="10" height="10" rx="1.5" />
                    <path d="M5 7l1.5 1.5L9 6" />
                  </svg>
                </IconButton>
              </>
            )}

            {/* Tool-unit management action */}
            {inToolUnitMgmt && (
              <IconButton onClick={handleCreateUnit} label="新建工具 unit">
                <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M7 2v10M2 7h10" />
                </svg>
              </IconButton>
            )}

            {/* Refresh — instances only */}
            {inInstances && (
              <IconButton onClick={handleRefreshInstances} label="刷新">
                <RefreshIcon spinning={refreshSpinning} />
              </IconButton>
            )}

            {/* Exit the active takeover */}
            <IconButton onClick={handleExit} label={takeoverExitLabel}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </IconButton>
          </>
        ) : (
          <>
            {/* Artifacts */}
            <IconButton onClick={toggleArtifactPanel} label="文件面板">
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

            {/* Skill management */}
            <IconButton onClick={handleManageSkills} label="技能管理">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6.5 2l1 2.7 2.7 1-2.7 1-1 2.7-1-2.7-2.7-1 2.7-1z" />
                <path d="M11.5 9.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" />
              </svg>
            </IconButton>

            {/* Exit skill management */}
            {inSkills && (
              <IconButton onClick={handleExit} label="退出技能管理">
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
            {inObservability ? '会话监控' : inUserMgmt ? '用户管理' : inToolUnitMgmt ? '工具管理' : inSkills ? '技能管理' : inInstances ? '实例监控' : APP_NAME}
          </h1>
          {!inObservability && !inUserMgmt && !inToolUnitMgmt && !inSkills && !inInstances && (
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
              onClick={handleExit}
              className={navRowDangerClass}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M9 3H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h5M7 8h6m0 0l-2-2m2 2l-2 2" />
              </svg>
              退出监控
            </button>
          </>
        ) : inAdminTakeover ? (
          <>
            {/* User-management actions — hoisted from UserManagementPanel */}
            {inUserMgmt && (
              <>
                <button onClick={handleCreateUser} className={navRowClass}>
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M7 2v10M2 7h10" />
                  </svg>
                  新建用户
                </button>
                <button onClick={handleBulkImport} className={navRowClass}>
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M7 2v8M3 8l4 4 4-4M2 13h10" />
                  </svg>
                  批量导入
                </button>
                <button onClick={handleDeptManager} className={navRowClass}>
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M2 3h10M2 7h10M2 11h6" />
                  </svg>
                  管理部门
                </button>
                <button onClick={handleToggleSelection} className={navRowClass}>
                  <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <rect x="2" y="2" width="10" height="10" rx="1.5" />
                    <path d="M5 7l1.5 1.5L9 6" />
                  </svg>
                  批量管理
                </button>
              </>
            )}

            {/* Tool-unit management action — hoisted from ToolUnitManagementPanel */}
            {inToolUnitMgmt && (
              <button onClick={handleCreateUnit} className={navRowClass}>
                <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M7 2v10M2 7h10" />
                </svg>
                新建工具 unit
              </button>
            )}

            {/* Refresh — instances only (mirrors observability's 刷新对话) */}
            {inInstances && (
              <button
                onClick={handleRefreshInstances}
                className={navRowClass}
              >
                <RefreshIcon size={16} spinning={refreshSpinning} />
                刷新
              </button>
            )}
            <button
              onClick={handleExit}
              className={navRowDangerClass}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M9 3H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h5M7 8h6m0 0l-2-2m2 2l-2 2" />
              </svg>
              {takeoverExitLabel}
            </button>
          </>
        ) : (
          <>
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
            <button
              onClick={handleManageSkills}
              className={navRowClass}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6.5 2l1 2.7 2.7 1-2.7 1-1 2.7-1-2.7-2.7-1 2.7-1z" />
                <path d="M11.5 9.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" />
              </svg>
              技能管理
            </button>
            {inSkills && (
              <button
                onClick={handleExit}
                className={navRowDangerClass}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M9 3H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h5M7 8h6m0 0l-2-2m2 2l-2 2" />
                </svg>
                退出技能管理
              </button>
            )}
          </>
        )}
      </div>

      {/* Conversation list — hidden in the full-takeover admin modes (they have
          nothing to do with conversations); observability keeps its admin list. */}
      {!inAdminTakeover && (
        <>
          <div className="px-5 pt-2 pb-1 text-xs font-semibold text-text-tertiary dark:text-text-tertiary-dark">
            对话列表
          </div>
          {inObservability ? <AdminConversationList /> : <ConversationList />}
        </>
      )}

      {/* Spacer — the conversation lists carry flex-1; without them the bottom
          section would float up, so pin it down in the takeover modes. */}
      {inAdminTakeover && <div className="flex-1" />}

      {/* Notifications + user menu at bottom */}
      <div className="px-3 pb-3 pt-2 space-y-2">
        {!inObservability && !inAdminTakeover && <StorageBar />}
        <NotificationCenter />
        <UserMenu />
      </div>
      <BrandingFooter variant="sidebar" />
    </div>
  );
}

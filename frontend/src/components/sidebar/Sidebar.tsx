'use client';

import { useState, useCallback } from 'react';
import { useUIStore, type UserMgmtRightView } from '@/stores/uiStore';
import { useAuthStore } from '@/stores/authStore';
import { useChat } from '@/hooks/useChat';
import ConversationList from './ConversationList';
import AdminConversationList from './AdminConversationList';
import NotificationConfigList from './NotificationConfigList';
import UserMenu from './UserMenu';
import NotificationCenter from './NotificationCenter';
import BrandingFooter from '@/components/BrandingFooter';
import { PillBadge } from '@/components/ui/PillBadge';
import { APP_NAME, APP_TAGLINE } from '@/lib/branding';
import { MENU_ROW_HOVER, MENU_ROW_DANGER_HOVER } from '@/lib/styles';

function IconButton({
  onClick,
  label,
  children,
  disabled = false,
}: {
  onClick: () => void;
  label: string;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`w-11 h-11 md:w-10 md:h-10 flex items-center justify-center rounded-md text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent ${MENU_ROW_HOVER}`}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

// Plain text-row nav buttons (no border/fill) — icon + label with a subtle hover highlight.
const navRowClass =
  `w-full min-h-11 md:min-h-0 flex items-center gap-2.5 px-2 py-2.5 md:py-1.5 font-medium text-text-primary dark:text-text-primary-dark rounded-lg ${MENU_ROW_HOVER}`;

const navRowDangerClass =
  `w-full min-h-11 md:min-h-0 flex items-center gap-2.5 px-2 py-2.5 md:py-1.5 font-medium text-status-error rounded-lg ${MENU_ROW_DANGER_HOVER}`;

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

// Admin-takeover action icons — shared by the collapsed icon bar and the
// expanded nav rows so the two render paths can't drift (mirrors RefreshIcon).
const iconProps = {
  width: 16,
  height: 16,
  viewBox: '0 0 14 14',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
} as const;

const PlusIcon = () => (
  <svg {...iconProps}>
    <path d="M7 2v10M2 7h10" />
  </svg>
);

const SaveIcon = () => (
  <svg {...iconProps} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 2.5h7l2 2V12a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 2 12V3a.5.5 0 0 1 .5-.5H3Z" />
    <path d="M4 2.5v3h6v-3M4 12.5V9h6v3.5" />
  </svg>
);

const BulkImportIcon = () => (
  <svg {...iconProps}>
    <path d="M7 2v8M3 8l4 4 4-4M2 13h10" />
  </svg>
);

const DeptIcon = () => (
  <svg {...iconProps}>
    <path d="M2 3h10M2 7h10M2 11h6" />
  </svg>
);

const SelectIcon = () => (
  <svg {...iconProps}>
    <rect x="2" y="2" width="10" height="10" rx="1.5" />
    <path d="M5 7l1.5 1.5L9 6" />
  </svg>
);

interface SidebarProps {
  variant?: 'desktop' | 'drawer';
  onNavigate?: () => void;
}

export default function Sidebar({
  variant = 'desktop',
  onNavigate = () => {},
}: SidebarProps) {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);
  const requestComposerFocus = useUIStore((s) => s.requestComposerFocus);

  const activeMode = useUIStore((s) => s.activeMode);
  const setActiveMode = useUIStore((s) => s.setActiveMode);
  const setObservabilityBrowseVisible = useUIStore((s) => s.setObservabilityBrowseVisible);
  const triggerObservabilityRefresh = useUIStore((s) => s.triggerObservabilityRefresh);
  const triggerInstancesRefresh = useUIStore((s) => s.triggerInstancesRefresh);
  const notificationConfigDirty = useUIStore((s) => s.notificationConfigDirty);
  const notificationConfigSaving = useUIStore((s) => s.notificationConfigSaving);
  const notificationConfigLoading = useUIStore((s) => s.notificationConfigLoading);
  const requestNotificationConfigCreate = useUIStore((s) => s.requestNotificationConfigCreate);
  const requestNotificationConfigRefresh = useUIStore((s) => s.requestNotificationConfigRefresh);
  const requestNotificationConfigSave = useUIStore((s) => s.requestNotificationConfigSave);
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
    requestComposerFocus();
    onNavigate();
  };

  const handleToggleArtifactPanel = () => {
    toggleArtifactPanel();
    onNavigate();
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

  const handleRefreshNotifications = useCallback(() => {
    requestNotificationConfigRefresh();
    setRefreshSpinning(true);
    setTimeout(() => setRefreshSpinning(false), 600);
  }, [requestNotificationConfigRefresh]);

  const handleSearchChat = () => {
    setActiveMode('conversationBrowser');
    onNavigate();
  };

  const handleManageSkills = () => {
    setActiveMode('skills');
    onNavigate();
  };

  const handleSearchAdmin = () => {
    setObservabilityBrowseVisible(true);
    onNavigate();
  };

  const handleExit = () => {
    setActiveMode('none');
    onNavigate();
  };

  // Opening a form leaves selection mode — the old middle-panel button row was
  // replaced wholesale while selecting, so form + selection could never coexist;
  // keep that single-active invariant now that the buttons persist in the sidebar.
  const openUserView = (view: UserMgmtRightView) => {
    if (selectionMode) exitSelectionMode();
    setUserManagementRightView(view);
    onNavigate();
  };
  const handleCreateUser = () => openUserView({ type: 'create-user' });
  const handleBulkImport = () => openUserView({ type: 'bulk-import' });
  const handleDeptManager = () => openUserView({ type: 'dept-manager' });
  // Toggle: 批量管理 enters selection mode; pressing it again (or the middle
  // toolbar's 退出 / Esc) leaves it — keeps the sidebar in sync with the panel.
  const handleToggleSelection = () => {
    (selectionMode ? exitSelectionMode() : enterSelectionMode());
    onNavigate();
  };
  const handleCreateUnit = () => {
    setToolUnitRightView({ type: 'create-unit' });
    onNavigate();
  };
  const handleImportUnit = () => {
    setToolUnitRightView({ type: 'import-unit' });
    onNavigate();
  };

  const inObservability = activeMode === 'observability' && isAdmin;
  // While a master-detail mode owns the right panel (force-shown on desktop,
  // force-hidden on mobile), the artifact toggle would just flip a hidden
  // store flag that ThreeColumnLayout's forceArtifactVisible overrides —
  // the button looks broken and leaks state across exit. Hide it here.
  const inUserMgmt = activeMode === 'userManagement' && isAdmin;
  // Tool-unit management is the same master-detail shape as user-mgmt.
  const inToolUnitMgmt = activeMode === 'toolUnit' && isAdmin;
  const inDepartmentAccess = activeMode === 'departmentAccess' && isAdmin;
  // Fleet instances (Phase C) — center takeover, admin-only, like observability
  // but without the conversation search/refresh actions.
  const inInstances = activeMode === 'instances' && isAdmin;
  const inNotificationConfig = activeMode === 'notificationConfig' && isAdmin;

  // 「全接管」admin 模式:实例监控/工具管理/用户管理/部门授权。这些把中间/右面板整个接管,
  // 与对话无关 → 侧栏隐藏对话列表 + 文件面板/搜索对话/新建对话/技能管理,只留退出
  // (实例监控额外留一个刷新)。会话监控(observability)不算 —— 它本就是看对话的,
  // 保留 admin 对话列表 + 搜索/刷新。
  const inAdminTakeover = inUserMgmt || inToolUnitMgmt || inDepartmentAccess || inInstances || inNotificationConfig;
  const takeoverExitLabel = inUserMgmt
    ? '退出用户管理'
    : inToolUnitMgmt
      ? '退出工具管理'
      : inDepartmentAccess
        ? '退出部门授权'
        : inInstances
          ? '退出实例监控'
          : '退出通知管理';

  // ── Collapsed: 48px icon bar ──
  if (variant === 'desktop' && sidebarCollapsed) {
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
                  <PlusIcon />
                </IconButton>
                <IconButton onClick={handleBulkImport} label="批量导入">
                  <BulkImportIcon />
                </IconButton>
                <IconButton onClick={handleDeptManager} label="管理部门">
                  <DeptIcon />
                </IconButton>
                <IconButton onClick={handleToggleSelection} label="批量管理">
                  <SelectIcon />
                </IconButton>
              </>
            )}

            {/* Tool-unit management action */}
            {inToolUnitMgmt && (
              <>
                <IconButton onClick={handleCreateUnit} label="新建工具 unit">
                  <PlusIcon />
                </IconButton>
                <IconButton onClick={handleImportUnit} label="导入工具 seed">
                  <BulkImportIcon />
                </IconButton>
              </>
            )}

            {/* Refresh — instances only */}
            {inInstances && (
              <IconButton onClick={handleRefreshInstances} label="刷新">
                <RefreshIcon spinning={refreshSpinning} />
              </IconButton>
            )}

            {/* Notification config actions */}
            {inNotificationConfig && (
              <>
                <IconButton onClick={requestNotificationConfigCreate} label="新建通知">
                  <PlusIcon />
                </IconButton>
                <IconButton onClick={handleRefreshNotifications} label="刷新通知" disabled={notificationConfigLoading}>
                  <RefreshIcon spinning={refreshSpinning} />
                </IconButton>
                <IconButton
                  onClick={requestNotificationConfigSave}
                  label="保存通知"
                  disabled={!notificationConfigDirty || notificationConfigSaving}
                >
                  <SaveIcon />
                </IconButton>
              </>
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
            <IconButton onClick={handleToggleArtifactPanel} label="文件面板">
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
          </>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Notifications (auto-hides when empty) */}
        <NotificationCenter collapsed />

        {/* User menu */}
        <UserMenu collapsed onNavigate={onNavigate} />
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
            {inObservability ? '会话监控' : inUserMgmt ? '用户管理' : inToolUnitMgmt ? '工具管理' : inDepartmentAccess ? '部门授权' : inInstances ? '实例监控' : inNotificationConfig ? '通知管理' : APP_NAME}
          </h1>
          {!inObservability && !inUserMgmt && !inToolUnitMgmt && !inDepartmentAccess && !inInstances && !inNotificationConfig && (
            <p className="text-xs text-text-secondary dark:text-text-secondary-dark">
              {APP_TAGLINE}
            </p>
          )}
        </div>
        <IconButton
          onClick={variant === 'drawer' ? onNavigate : toggleSidebar}
          label="收起侧栏"
        >
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
                  <PlusIcon />
                  新建用户
                </button>
                <button onClick={handleBulkImport} className={navRowClass}>
                  <BulkImportIcon />
                  批量导入
                </button>
                <button onClick={handleDeptManager} className={navRowClass}>
                  <DeptIcon />
                  管理部门
                </button>
                <button onClick={handleToggleSelection} className={navRowClass}>
                  <SelectIcon />
                  批量管理
                </button>
              </>
            )}

            {/* Tool-unit management action — hoisted from ToolUnitManagementPanel */}
            {inToolUnitMgmt && (
              <>
                <button onClick={handleCreateUnit} className={navRowClass}>
                  <PlusIcon />
                  新建工具 unit
                </button>
                <button onClick={handleImportUnit} className={navRowClass}>
                  <BulkImportIcon />
                  导入 seed
                </button>
              </>
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

            {/* Notification config actions — hoisted from NotificationConfigPanel */}
            {inNotificationConfig && (
              <>
                <button onClick={requestNotificationConfigCreate} className={navRowClass}>
                  <PlusIcon />
                  新建通知
                </button>
                <button
                  onClick={handleRefreshNotifications}
                  disabled={notificationConfigLoading}
                  className={`${navRowClass} disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent`}
                >
                  <RefreshIcon size={16} spinning={refreshSpinning} />
                  刷新
                </button>
                <button
                  onClick={requestNotificationConfigSave}
                  disabled={!notificationConfigDirty || notificationConfigSaving}
                  className={`${navRowClass} disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent`}
                >
                  <SaveIcon />
                  <span>{notificationConfigSaving ? '保存中' : '保存'}</span>
                  {notificationConfigDirty && (
                    <PillBadge tone="warning">未保存</PillBadge>
                  )}
                </button>
              </>
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
              onClick={handleToggleArtifactPanel}
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
          {inObservability ? <AdminConversationList /> : <ConversationList onNavigate={onNavigate} />}
        </>
      )}

      {inNotificationConfig && <NotificationConfigList />}

      {/* Spacer — the conversation lists carry flex-1; without them the bottom
          section would float up, so pin it down in the takeover modes. */}
      {inAdminTakeover && !inNotificationConfig && <div className="flex-1" />}

      {/* Notifications + user menu at bottom */}
      <div className="px-3 pb-3 pt-2 space-y-2">
        <NotificationCenter />
        <UserMenu onNavigate={onNavigate} />
      </div>
      <BrandingFooter variant="sidebar" />
    </div>
  );
}

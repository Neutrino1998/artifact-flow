'use client';

import dynamic from 'next/dynamic';
import ThreeColumnLayout from '@/components/layout/ThreeColumnLayout';
import Sidebar from '@/components/sidebar/Sidebar';
import ChatPanel from '@/components/chat/ChatPanel';
import ArtifactPanel from '@/components/artifact/ArtifactPanel';
import ErrorBoundary from '@/components/ErrorBoundary';
import AuthGuard from '@/components/AuthGuard';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useAuthStore } from '@/stores/authStore';
import { useMediaQuery, BREAKPOINTS } from '@/hooks/useMediaQuery';
import PermissionModal from '@/components/layout/PermissionModal';
import { artifactVisibilityOverride } from '@/lib/panelMode';

function DetailPanelLoading() {
  return (
    <div className="h-full flex items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
      加载详情中...
    </div>
  );
}

const UserManagementDetailPanel = dynamic(
  () => import('@/features/admin/users/UserManagementDetailPanel'),
  { loading: DetailPanelLoading },
);
const ToolUnitDetailPanel = dynamic(
  () => import('@/features/admin/tool-units/ToolUnitDetailPanel'),
  { loading: DetailPanelLoading },
);
const SkillPreviewPanel = dynamic(
  () => import('@/features/skills/SkillPreviewPanel'),
  { loading: DetailPanelLoading },
);

export default function Home() {
  const permissionRequest = useStreamStore((s) => s.permissionRequest);
  const activeMode = useUIStore((s) => s.activeMode);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const isMd = useMediaQuery(BREAKPOINTS.md);
  const userMgmtMode = activeMode === 'userManagement' && isAdmin;
  const toolUnitMode = activeMode === 'toolUnit' && isAdmin;
  const skillMode = activeMode === 'skills';

  // Right-panel visibility override (see ThreeColumnLayout's prop doc):
  //   desktop master-detail (user-mgmt / tool-unit) → true  (force-show)
  //   mobile  master-detail → false (force-hide; overrides any prior artifactPanelVisible
  //                              so opening admin management while the artifact drawer
  //                              was open does not bury the master list under an empty
  //                              detail panel — admin work isn't a mobile use case)
  //   full-screen conversation-independent takeovers → false
  //   neither → undefined (defer to user-controlled artifactPanelVisible)
  const forceArtifactVisible = artifactVisibilityOverride(activeMode, isAdmin, isMd);

  const rightContent = skillMode ? (
    <ErrorBoundary fallbackLabel="技能说明面板出错了">
      <SkillPreviewPanel />
    </ErrorBoundary>
  ) : userMgmtMode ? (
    <ErrorBoundary fallbackLabel="用户管理详情面板出错了">
      <UserManagementDetailPanel />
    </ErrorBoundary>
  ) : toolUnitMode ? (
    <ErrorBoundary fallbackLabel="工具管理详情面板出错了">
      <ToolUnitDetailPanel />
    </ErrorBoundary>
  ) : (
    <ErrorBoundary fallbackLabel="文件面板出错了">
      <ArtifactPanel />
    </ErrorBoundary>
  );

  return (
    <AuthGuard>
      <ThreeColumnLayout
        sidebar={({ variant, onNavigate }) => (
          <Sidebar variant={variant} onNavigate={onNavigate} />
        )}
        chat={
          <ErrorBoundary fallbackLabel="Chat panel encountered an error">
            <ChatPanel />
          </ErrorBoundary>
        }
        artifact={rightContent}
        rightPanelLabel={skillMode ? '技能说明' : '文件面板'}
        forceArtifactVisible={forceArtifactVisible}
      />
      {permissionRequest && <PermissionModal />}
    </AuthGuard>
  );
}

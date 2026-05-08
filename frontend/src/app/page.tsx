'use client';

import ThreeColumnLayout from '@/components/layout/ThreeColumnLayout';
import Sidebar from '@/components/sidebar/Sidebar';
import ChatPanel from '@/components/chat/ChatPanel';
import ArtifactPanel from '@/components/artifact/ArtifactPanel';
import UserManagementDetailPanel from '@/components/chat/UserManagementDetailPanel';
import ErrorBoundary from '@/components/ErrorBoundary';
import AuthGuard from '@/components/AuthGuard';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useAuthStore } from '@/stores/authStore';
import { useMediaQuery, BREAKPOINTS } from '@/hooks/useMediaQuery';
import PermissionModal from '@/components/layout/PermissionModal';

export default function Home() {
  const permissionRequest = useStreamStore((s) => s.permissionRequest);
  const userManagementVisible = useUIStore((s) => s.userManagementVisible);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const isMd = useMediaQuery(BREAKPOINTS.md);
  const userMgmtMode = userManagementVisible && isAdmin;

  // Right-panel visibility override (see ThreeColumnLayout's prop doc):
  //   desktop user-mgmt → true  (master-detail force-show)
  //   mobile  user-mgmt → false (force-hide; overrides any prior artifactPanelVisible
  //                              so opening user management while the artifact drawer
  //                              was open does not bury the user list under an empty
  //                              detail panel — admin work isn't a mobile use case)
  //   not in user-mgmt  → undefined (defer to user-controlled artifactPanelVisible)
  const forceArtifactVisible = userMgmtMode ? isMd : undefined;

  const rightContent = userMgmtMode ? (
    <ErrorBoundary fallbackLabel="用户管理详情面板出错了">
      <UserManagementDetailPanel />
    </ErrorBoundary>
  ) : (
    <ErrorBoundary fallbackLabel="文稿面板出错了">
      <ArtifactPanel />
    </ErrorBoundary>
  );

  return (
    <AuthGuard>
      <ThreeColumnLayout
        sidebar={<Sidebar />}
        chat={
          <ErrorBoundary fallbackLabel="Chat panel encountered an error">
            <ChatPanel />
          </ErrorBoundary>
        }
        artifact={rightContent}
        forceArtifactVisible={forceArtifactVisible}
      />
      {permissionRequest && <PermissionModal />}
    </AuthGuard>
  );
}

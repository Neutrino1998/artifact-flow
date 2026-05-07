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

  // Master-detail right panel is desktop-only (>= md). On mobile the overlay
  // would cover the entire user list with no usable dismiss path; admin work
  // (CSV import, dept tree, batch ops) doesn't fit narrow screens anyway, so
  // we treat the detail panel as desktop-only and let mobile admins fall back
  // to the inline list interactions.
  const forceArtifactVisible = userMgmtMode && isMd;

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

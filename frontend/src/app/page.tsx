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
import PermissionModal from '@/components/layout/PermissionModal';

export default function Home() {
  const permissionRequest = useStreamStore((s) => s.permissionRequest);
  const userManagementVisible = useUIStore((s) => s.userManagementVisible);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const userMgmtMode = userManagementVisible && isAdmin;

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
        forceArtifactVisible={userMgmtMode}
      />
      {permissionRequest && <PermissionModal />}
    </AuthGuard>
  );
}

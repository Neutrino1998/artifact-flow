'use client';

import ThreeColumnLayout from '@/components/layout/ThreeColumnLayout';
import Sidebar from '@/components/sidebar/Sidebar';
import ChatPanel from '@/components/chat/ChatPanel';
import ArtifactPanel from '@/components/artifact/ArtifactPanel';
import ErrorBoundary from '@/components/ErrorBoundary';
import { useStreamStore } from '@/stores/streamStore';
import PermissionModal from '@/components/layout/PermissionModal';

export default function Home() {
  const permissionRequest = useStreamStore((s) => s.permissionRequest);

  return (
    <>
      <ThreeColumnLayout
        sidebar={<Sidebar />}
        chat={
          <ErrorBoundary fallbackLabel="Chat panel encountered an error">
            <ChatPanel />
          </ErrorBoundary>
        }
        artifact={
          <ErrorBoundary fallbackLabel="文稿面板出错了">
            <ArtifactPanel />
          </ErrorBoundary>
        }
      />
      {permissionRequest && <PermissionModal />}
    </>
  );
}

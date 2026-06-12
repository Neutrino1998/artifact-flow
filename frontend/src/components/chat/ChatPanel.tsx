'use client';

import { useState, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useStagedFilesStore } from '@/stores/stagedFilesStore';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import WelcomeTips from './WelcomeTips';
import StreamingMessage from './StreamingMessage';
import ErrorFlowBlock from './ErrorFlowBlock';
import UserMessage from './UserMessage';
import ConversationBrowser from './ConversationBrowser';
import UserManagementPanel from './UserManagementPanel';
import ObservabilityPanel from './ObservabilityPanel';
import { useAuthStore } from '@/stores/authStore';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 6) return '夜深了';
  if (hour < 12) return '早上好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

export default function ChatPanel() {
  const current = useConversationStore((s) => s.current);
  const currentLoading = useConversationStore((s) => s.currentLoading);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const pendingUserMessage = useStreamStore((s) => s.pendingUserMessage);
  const pendingUserFiles = useStreamStore((s) => s.pendingUserFiles);
  const sendError = useStreamStore((s) => s.sendError);

  const conversationBrowserVisible = useUIStore((s) => s.conversationBrowserVisible);
  const userManagementVisible = useUIStore((s) => s.userManagementVisible);
  const observabilityVisible = useUIStore((s) => s.observabilityVisible);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');

  const addFiles = useStagedFilesStore((s) => s.addFiles);
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    // Attachments ride a new message, not an in-flight turn — ignore drops
    // while streaming (matches the disabled attach button during streaming).
    if (isStreaming) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) addFiles(files);
  }, [isStreaming, addFiles]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    // Only set false when leaving the container (not entering children)
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragOver(false);
  }, []);

  if (observabilityVisible && isAdmin) {
    return <ObservabilityPanel />;
  }

  if (userManagementVisible && isAdmin) {
    return <UserManagementPanel />;
  }

  if (conversationBrowserVisible) {
    return <ConversationBrowser />;
  }

  if (currentLoading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-chat dark:bg-chat-dark">
        <div className="text-text-tertiary dark:text-text-tertiary-dark">
          加载对话中...
        </div>
      </div>
    );
  }

  // Show input even when no conversation — allows starting new conversations
  return (
    <div
      className="flex-1 flex flex-col min-h-0 relative bg-chat dark:bg-chat-dark"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {current ? (
        <MessageList />
      ) : isStreaming ? (
        // New conversation, first message in flight — `current` only lands when
        // refreshAfterComplete runs at terminal, so MessageList (gated on
        // current) would render nothing. Render the pending bubble + stream
        // inline here, but via the same UserMessage component as the persisted
        // and post-refresh-live paths so all three share one layout source.
        // `!== null` (not truthy) so empty content still renders a bubble for
        // compact-only / upload-only sends — was the source of the "first
        // upload-only message has no bubble" bug pre-unification.
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto pl-8 pr-4 py-6 space-y-6">
            {pendingUserMessage !== null && (
              <UserMessage
                content={pendingUserMessage}
                messageId=""
                parentId={null}
                pending
                attachments={pendingUserFiles?.map((filename) => ({ filename }))}
              />
            )}
            <StreamingMessage />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center gap-2">
          <div className="relative">
            <img
              src="/cat-sleep-light.svg"
              alt=""
              className="dark:hidden w-72 translate-y-6"
            />
            <img
              src="/cat-sleep-dark.svg"
              alt=""
              className="hidden dark:block w-72 translate-y-6"
            />
          </div>
          <div className="text-text-secondary dark:text-text-secondary-dark text-3xl font-semibold">
            {getGreeting()}，有什么可以帮你的？
          </div>
          <WelcomeTips />
        </div>
      )}

      {/* Pre-stream send failures (the POST /api/v1/chat phase — nginx 413,
          backend 422 oversize / unparseable upload, or a network drop) fail
          BEFORE connect() flips isStreaming, so no stream/message exists to host
          the error and the user just sees the spinner blink. They land in a
          dedicated `sendError` (NOT the stream's `error`, which already renders
          inside the message flow) so surfacing them here can't double-render a
          terminal SSE error. Cleared on the next send, on startStream, and on
          conversation switch (reset), so it can't linger or cross conversations. */}
      {sendError && (
        // relative z-10 lifts the banner above MessageInput's top fade overlay
        // (an absolutely-positioned -top-6 gradient, later in DOM so it would
        // otherwise paint over the banner's lower edge). ErrorFlowBlock's solid
        // bg-chat then fully covers, so no half-faded card.
        <div className="relative z-10 px-4 pb-2">
          <div className="max-w-3xl mx-auto">
            <ErrorFlowBlock message={sendError} />
          </div>
        </div>
      )}

      <MessageInput />

      {/* Drag overlay */}
      {isDragOver && (
        <div className="absolute inset-0 bg-accent/10 border-2 border-dashed border-accent rounded-xl flex items-center justify-center z-50 pointer-events-none">
          <div className="bg-surface dark:bg-surface-dark px-6 py-4 rounded-xl shadow-float text-center">
            <div className="text-accent text-lg font-semibold">
              释放以上传文件
            </div>
            <div className="text-text-tertiary dark:text-text-tertiary-dark text-xs mt-1">
              支持 .docx / .pdf / .txt / .md / .csv / 代码文件
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

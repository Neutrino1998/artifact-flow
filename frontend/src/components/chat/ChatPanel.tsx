'use client';

import { useState, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useUpload } from '@/hooks/useUpload';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import WelcomeTips from './WelcomeTips';
import StreamingMessage from './StreamingMessage';
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

  const conversationBrowserVisible = useUIStore((s) => s.conversationBrowserVisible);
  const userManagementVisible = useUIStore((s) => s.userManagementVisible);
  const observabilityVisible = useUIStore((s) => s.observabilityVisible);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');

  const upload = useUpload();
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    await upload(Array.from(e.dataTransfer.files));
  }, [upload]);

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
        // New conversation: no history yet, but stream is active
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto pl-8 pr-4 py-6 space-y-6">
            {pendingUserMessage && (
              <div className="flex justify-end">
                <div className="max-w-[80%] bg-panel-accent dark:bg-surface-dark rounded-bubble px-4 py-3 text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
                  {pendingUserMessage}
                </div>
              </div>
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

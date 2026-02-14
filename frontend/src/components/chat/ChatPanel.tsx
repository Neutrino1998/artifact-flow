'use client';

import { useMemo } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import StreamingMessage from './StreamingMessage';

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

  if (currentLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-text-tertiary dark:text-text-tertiary-dark text-sm">
          加载对话中...
        </div>
      </div>
    );
  }

  // Show input even when no conversation — allows starting new conversations
  return (
    <div className="flex-1 flex flex-col min-h-0">
      {current ? (
        <MessageList />
      ) : isStreaming ? (
        // New conversation: no history yet, but stream is active
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
            {pendingUserMessage && (
              <div className="flex justify-end">
                <div className="max-w-[80%] bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-bubble px-4 py-3 text-sm text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
                  {pendingUserMessage}
                </div>
              </div>
            )}
            <StreamingMessage />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center gap-2">
          <div className="text-text-secondary dark:text-text-secondary-dark text-3xl font-semibold">
            {getGreeting()}，有什么可以帮你的？
          </div>
          <div className="text-text-tertiary dark:text-text-tertiary-dark text-sm">
            开始对话，探索更多可能
          </div>
        </div>
      )}
      <MessageInput />
    </div>
  );
}

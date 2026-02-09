'use client';

import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import StreamingMessage from './StreamingMessage';

export default function ChatPanel() {
  const current = useConversationStore((s) => s.current);
  const currentLoading = useConversationStore((s) => s.currentLoading);
  const isStreaming = useStreamStore((s) => s.isStreaming);

  if (currentLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-text-tertiary dark:text-text-tertiary-dark text-sm">
          Loading conversation...
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
          <div className="max-w-3xl mx-auto px-4 py-6">
            <StreamingMessage />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-text-tertiary dark:text-text-tertiary-dark text-sm">
            Start a new conversation
          </div>
        </div>
      )}
      <MessageInput />
    </div>
  );
}

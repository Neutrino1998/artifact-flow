'use client';

import { useEffect, useRef } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import UserMessage from './UserMessage';
import AssistantMessage from './AssistantMessage';
import StreamingMessage from './StreamingMessage';
import BranchNavigator from './BranchNavigator';

export default function MessageList() {
  const branchPath = useConversationStore((s) => s.branchPath);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when conversation loads or new messages arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [branchPath.length]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
        {branchPath.map((node) => (
          <div key={node.id} className="space-y-6">
            {/* Branch navigator if siblings exist */}
            {node.siblingCount > 1 && (
              <BranchNavigator
                messageId={node.id}
                currentIndex={node.siblingIndex}
                totalSiblings={node.siblingCount}
              />
            )}

            {/* User message */}
            <UserMessage
              content={node.content}
              messageId={node.id}
              parentId={node.parent_id}
            />

            {/* Assistant response */}
            {node.response && (
              <AssistantMessage content={node.response} messageId={node.id} />
            )}
          </div>
        ))}

        {/* Streaming message */}
        {isStreaming && <StreamingMessage />}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

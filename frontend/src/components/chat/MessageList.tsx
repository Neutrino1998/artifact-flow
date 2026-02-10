'use client';

import { useEffect, useRef, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore, selectStreamContent } from '@/stores/streamStore';
import UserMessage from './UserMessage';
import AssistantMessage from './AssistantMessage';
import StreamingMessage from './StreamingMessage';
import BranchNavigator from './BranchNavigator';

export default function MessageList() {
  const branchPath = useConversationStore((s) => s.branchPath);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const lastContent = useStreamStore(selectStreamContent);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  // Track if user has scrolled up from bottom
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const threshold = 100;
    userScrolledUp.current = el.scrollHeight - el.scrollTop - el.clientHeight > threshold;
  }, []);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [branchPath.length]);

  // Auto-scroll during streaming (RAF-throttled, only if near bottom)
  useEffect(() => {
    if (!isStreaming || userScrolledUp.current) return;
    const id = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    });
    return () => cancelAnimationFrame(id);
  }, [isStreaming, lastContent]);

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
        {branchPath.map((node) => (
          <div key={node.id} className="space-y-4">
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

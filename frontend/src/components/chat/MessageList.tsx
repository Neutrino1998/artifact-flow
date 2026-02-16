'use client';

import { useEffect, useRef, useMemo } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import UserMessage from './UserMessage';
import AssistantMessage from './AssistantMessage';
import StreamingMessage from './StreamingMessage';

export default function MessageList() {
  const branchPath = useConversationStore((s) => s.branchPath);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const streamParentId = useStreamStore((s) => s.streamParentId);
  const pendingUserMessage = useStreamStore((s) => s.pendingUserMessage);
  const bottomRef = useRef<HTMLDivElement>(null);

  // During rerun/edit streaming, truncate branchPath to show only
  // messages up to (and including) the branch parent
  const displayPath = useMemo(() => {
    if (!isStreaming || streamParentId === undefined) return branchPath;
    if (streamParentId === null) return []; // root rerun: show nothing before
    const idx = branchPath.findIndex((n) => n.id === streamParentId);
    if (idx === -1) return branchPath;
    return branchPath.slice(0, idx + 1);
  }, [branchPath, isStreaming, streamParentId]);

  // Auto-scroll to bottom when conversation loads or new messages arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [displayPath.length]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
        {displayPath.map((node, idx) => {
          const isLastBeforeStream = isStreaming && streamParentId !== undefined && idx === displayPath.length - 1;
          return (
            <div key={node.id} className="space-y-10">
              {/* User message */}
              <UserMessage
                content={node.content}
                messageId={node.id}
                parentId={node.parent_id}
                siblingIndex={node.siblingIndex}
                siblingCount={node.siblingCount}
              />

              {/* Assistant response (hide if this is the truncation point during rerun) */}
              {node.response && !isLastBeforeStream && (
                <AssistantMessage content={node.response} messageId={node.id} />
              )}
            </div>
          );
        })}

        {/* Show pending user message during streaming (before conversation refresh) */}
        {isStreaming && pendingUserMessage && (
          <div className="flex justify-end">
            <div className="max-w-[80%] bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-bubble px-4 py-3 text-sm text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
              {pendingUserMessage}
            </div>
          </div>
        )}

        {/* Streaming message */}
        {isStreaming && <StreamingMessage />}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

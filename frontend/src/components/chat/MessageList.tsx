'use client';

import { useEffect, useRef, useMemo } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import UserMessage from './UserMessage';
import AssistantMessage from './AssistantMessage';
import StreamingMessage from './StreamingMessage';

export default function MessageList() {
  const branchPath = useConversationStore((s) => s.branchPath);
  const currentId = useConversationStore((s) => s.current?.id);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const streamConversationId = useStreamStore((s) => s.conversationId);
  const streamParentId = useStreamStore((s) => s.streamParentId);
  const pendingUserMessage = useStreamStore((s) => s.pendingUserMessage);

  // Only show streaming UI if the active stream belongs to this conversation
  const isStreamingHere = isStreaming && streamConversationId === currentId;
  const bottomRef = useRef<HTMLDivElement>(null);

  // During rerun/edit streaming, truncate branchPath to show only
  // messages up to (and including) the branch parent
  const displayPath = useMemo(() => {
    if (!isStreamingHere || streamParentId === undefined) return branchPath;
    if (streamParentId === null) return []; // root rerun: show nothing before
    const idx = branchPath.findIndex((n) => n.id === streamParentId);
    if (idx === -1) return branchPath;
    return branchPath.slice(0, idx + 1);
  }, [branchPath, isStreamingHere, streamParentId]);

  // Auto-scroll to bottom when conversation loads or new messages arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [displayPath.length]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto pl-8 pr-4 py-6 space-y-12">
        {displayPath.map((node) => (
            <div key={node.id} className="space-y-10">
              {/* User message */}
              <UserMessage
                content={node.user_input}
                messageId={node.id}
                parentId={node.parent_id}
                siblingIndex={node.siblingIndex}
                siblingCount={node.siblingCount}
                userInputSummary={node.user_input_summary}
              />

              {/* Assistant response */}
              {node.response && (
                <AssistantMessage
                  content={node.response}
                  messageId={node.id}
                  responseSummary={node.response_summary}
                />
              )}
            </div>
          ))}

        {/* Show pending user message during streaming (before conversation refresh) */}
        {isStreamingHere && pendingUserMessage && (
          <div className="flex justify-end">
            <div className="max-w-[80%] bg-panel dark:bg-surface-dark rounded-bubble px-4 py-3 text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
              {pendingUserMessage}
            </div>
          </div>
        )}

        {/* Streaming message */}
        {isStreamingHere && <StreamingMessage />}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

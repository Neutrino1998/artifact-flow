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
  const pendingUserFiles = useStreamStore((s) => s.pendingUserFiles);

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
              {/* User message (persisted path). TWIN: the live pre-refresh bubble
                  below (search `pending`) renders the SAME UserMessage component, so
                  layout can't drift. The field SOURCES differ, though — any new
                  per-message field surfaced here from the persisted DTO must also be
                  mirrored into the live pending source (useChat.ts setPendingUser* +
                  streamStore), or it'll show on reload but not live. */}
              <UserMessage
                content={node.user_input}
                messageId={node.id}
                parentId={node.parent_id}
                siblingIndex={node.siblingIndex}
                siblingCount={node.siblingCount}
                attachments={node.uploaded_files}
              />

              {/* Assistant response */}
              {node.response && (
                <AssistantMessage
                  content={node.response}
                  messageId={node.id}
                  executionMetrics={node.execution_metrics as { total_duration_ms?: number | null; total_token_usage?: { total_tokens?: number | null } | null } | null | undefined}
                />
              )}
            </div>
          ))}

        {/* Live (pre-refresh) user bubble — same UserMessage component as the
            persisted path, just with pending=true to skip the edit/rerun/branch
            actions overlay. Single layout source means live and final can't
            drift. `!== null` (not truthy) so empty content still renders a
            bubble for compact-only / upload-only sends, matching the persisted
            view's behavior. endStream() flips isStreaming false before
            refreshAfterComplete fills branchPath, so the two never overlap. */}
        {isStreamingHere && pendingUserMessage !== null && (
          <UserMessage
            content={pendingUserMessage}
            messageId=""
            parentId={streamParentId ?? null}
            pending
            attachments={pendingUserFiles?.map((filename) => ({ filename }))}
          />
        )}

        {/* Streaming message — also renders the queued-state header while waiting
            on the backend concurrency semaphore (before agent_start). */}
        {isStreamingHere && <StreamingMessage />}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

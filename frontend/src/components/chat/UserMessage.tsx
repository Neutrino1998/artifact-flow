'use client';

import { memo, useState, useRef, useEffect, useCallback } from 'react';
import { useChat } from '@/hooks/useChat';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { useStreamStore } from '@/stores/streamStore';
import { CopyIcon } from '@/components/ui/CopyIcon';
import BranchNavigator from './BranchNavigator';

interface UserMessageProps {
  content: string;
  messageId: string;
  parentId: string | null;
  /**
   * Sibling info comes from branchPath, only available after the turn is
   * persisted + conversation refreshed. Optional with safe defaults so the
   * same component can render the live (pre-refresh) bubble during streaming.
   */
  siblingIndex?: number;
  siblingCount?: number;
  /**
   * Live (in-flight) render: no persistent message_id yet, no sibling info,
   * editing/rerun/branching all forbidden by definition (turn already running).
   * Suppresses the entire hover-actions overlay so live and persisted bubbles
   * share one layout source — preventing the live/final drift that comes from
   * maintaining two parallel JSX trees.
   */
  pending?: boolean;
  /**
   * Files the user attached this turn. Persisted path: MessageResponse.uploaded_files
   * (best-effort — absent for turns that failed before artifact flush). Live path:
   * filenames mirrored from the send-local staged files (streamStore.pendingUserFiles).
   */
  attachments?: { filename: string }[] | null;
}

function UserMessage({ content, messageId, parentId, siblingIndex = 0, siblingCount = 1, pending = false, attachments = null }: UserMessageProps) {
  const { copied, copy } = useCopyFeedback();
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(content);
  const isComposingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { sendMessage } = useChat();
  const isStreaming = useStreamStore((s) => s.isStreaming);

  useEffect(() => {
    if (editing && textareaRef.current) {
      const el = textareaRef.current;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 300) + 'px';
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [editing]);

  const handleCopy = () => copy(content);

  const handleEdit = () => {
    setEditContent(content);
    setEditing(true);
  };

  const handleCancelEdit = () => {
    setEditing(false);
    setEditContent(content);
  };

  const handleSubmitEdit = useCallback(async () => {
    const trimmed = editContent.trim();
    if (!trimmed || isStreaming) return;
    setEditing(false);
    // Send as a new branch from the parent of this message
    await sendMessage(trimmed, parentId);
  }, [editContent, isStreaming, sendMessage, parentId]);

  const handleRerun = useCallback(async () => {
    if (isStreaming) return;
    await sendMessage(content, parentId);
  }, [content, isStreaming, sendMessage, parentId]);

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
      e.preventDefault();
      handleSubmitEdit();
    } else if (e.key === 'Escape') {
      handleCancelEdit();
    }
  };

  if (editing) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] w-full">
          <div className="bg-panel dark:bg-surface-dark rounded-bubble overflow-hidden ring-1 ring-accent">
            <textarea
              ref={textareaRef}
              value={editContent}
              onChange={(e) => {
                setEditContent(e.target.value);
                const el = e.target;
                el.style.height = 'auto';
                el.style.height = Math.min(el.scrollHeight, 300) + 'px';
              }}
              onKeyDown={handleEditKeyDown}
              onCompositionStart={() => { isComposingRef.current = true; }}
              onCompositionEnd={() => { requestAnimationFrame(() => { isComposingRef.current = false; }); }}
              rows={1}
              className="w-full px-4 py-3 bg-transparent text-text-primary dark:text-text-primary-dark outline-none resize-none"
            />
            <div className="flex justify-end gap-2 px-3 pb-2">
              <button
                onClick={handleCancelEdit}
                className="px-3 py-1 text-xs text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleSubmitEdit}
                disabled={!editContent.trim() || isStreaming}
                className="px-3 py-1 text-xs bg-accent text-white rounded hover:bg-accent-hover disabled:opacity-40 transition-colors"
              >
                发送
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-end group">
      <div className="relative max-w-[80%]">
        <div className="bg-panel-accent dark:bg-surface-dark rounded-bubble px-4 py-3 text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
          {attachments && attachments.length > 0 && (
            <div className={`flex flex-wrap justify-end gap-1.5 ${content ? 'mb-2' : ''}`}>
              {attachments.map((f, i) => (
                <span
                  key={`${f.filename}-${i}`}
                  className="inline-flex items-center gap-1 max-w-[16rem] px-2 py-0.5 rounded bg-surface dark:bg-bg-dark text-xs text-text-secondary dark:text-text-secondary-dark"
                  title={f.filename}
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                  </svg>
                  <span className="truncate">{f.filename}</span>
                </span>
              ))}
            </div>
          )}
          {content}
        </div>
        {/* Action buttons and branch navigator on hover. Skipped entirely when
            pending — turn is in flight, none of these actions are valid yet. */}
        {!pending && (
        <div className="absolute -bottom-7 right-2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={handleEdit}
            disabled={isStreaming}
            className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
            aria-label="Edit message"
            title="编辑"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
            </svg>
          </button>
          <button
            onClick={handleRerun}
            disabled={isStreaming}
            className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
            aria-label="Rerun message"
            title="重新生成"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
              <path d="M21 3v5h-5" />
            </svg>
          </button>
          <button
            onClick={handleCopy}
            className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
            aria-label="Copy message"
            title={copied ? '已复制' : '复制'}
          >
            <CopyIcon copied={copied} />
          </button>
          {siblingCount > 1 && (
            <>
              <div className="w-px h-3 bg-border dark:bg-border-dark mx-0.5" />
              <BranchNavigator
                messageId={messageId}
                currentIndex={siblingIndex}
                totalSiblings={siblingCount}
              />
            </>
          )}
        </div>
        )}
      </div>
    </div>
  );
}

export default memo(UserMessage);

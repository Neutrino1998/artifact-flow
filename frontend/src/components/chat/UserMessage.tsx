'use client';

import { memo, useState, useRef, useEffect, useCallback } from 'react';
import { useChat } from '@/features/chat/runtime/useChat';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { useStreamStore } from '@/stores/streamStore';
import { BUTTON_PRIMARY } from '@/lib/styles';
import { CopyIcon } from '@/components/ui/CopyIcon';
import type { ActivatedSkillRef, ReferencedArtifactRef } from '@/types';
import { formatMessageDateTime } from '@/lib/time';
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
  /** Skills explicitly selected by the user for this turn (not cumulative/model-read). */
  activatedSkills?: ActivatedSkillRef[] | null;
  /** Existing conversation uploads explicitly referenced for this turn. */
  referencedArtifacts?: ReferencedArtifactRef[] | null;
  /** Persisted message creation time. */
  timestamp?: string | null;
}

function UserMessage({ content, messageId, parentId, siblingIndex = 0, siblingCount = 1, pending = false, attachments = null, activatedSkills = null, referencedArtifacts = null, timestamp = null }: UserMessageProps) {
  const { copied, copy } = useCopyFeedback();
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(content);
  const isComposingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { sendMessage } = useChat();
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const hasAttachments = Boolean(attachments?.length);
  const hasActivatedSkills = Boolean(activatedSkills?.length);
  const hasReferencedArtifacts = Boolean(referencedArtifacts?.length);
  const hasContextChips = hasAttachments || hasActivatedSkills || hasReferencedArtifacts;
  const formattedTimestamp = timestamp ? formatMessageDateTime(timestamp) : null;

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
                className={`${BUTTON_PRIMARY} px-3 py-1 text-xs rounded-lg`}
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
        {hasContextChips && (
          <div className={`flex flex-wrap justify-end gap-1.5 ${content ? 'mb-1.5' : ''}`}>
            {attachments?.map((f, i) => (
              <span
                key={`${f.filename}-${i}`}
                className="inline-flex min-w-0 items-center gap-1 max-w-[16rem] px-2 py-1 rounded-lg bg-panel-accent dark:bg-surface-dark text-xs text-text-secondary dark:text-text-secondary-dark"
                title={f.filename}
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
                <span className="min-w-0 truncate">{f.filename}</span>
              </span>
            ))}
            {referencedArtifacts?.map((artifact) => (
              <span
                key={artifact.id}
                className="inline-flex min-w-0 items-center gap-1 max-w-[16rem] px-2 py-1 rounded-lg bg-surface dark:bg-surface-dark border border-border dark:border-border-dark text-xs text-text-secondary dark:text-text-secondary-dark"
                title={`引用文件：${artifact.filename}`}
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                  <path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1" />
                  <path d="M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1" />
                </svg>
                <span className="shrink-0 text-text-tertiary dark:text-text-tertiary-dark">引用</span>
                <span className="min-w-0 truncate">{artifact.filename}</span>
              </span>
            ))}
            {activatedSkills?.map((skill) => (
              <span
                key={skill.slug}
                className="inline-flex min-w-0 items-center gap-1 max-w-[16rem] px-2 py-1 rounded-lg bg-accent/10 border border-accent/30 text-xs text-accent"
                title={`已激活技能：${skill.name}`}
              >
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                  <path d="M6.5 2l1 2.7 2.7 1-2.7 1-1 2.7-1-2.7-2.7-1 2.7-1z" />
                  <path d="M11.5 9.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" />
                </svg>
                <span className="min-w-0 truncate">{skill.name}</span>
              </span>
            ))}
          </div>
        )}
        {/* Attachment/skill-only messages use chips as their visible content.
            Compact-only (no text or chips) keeps its legacy empty bubble. */}
        {(content || !hasContextChips) && (
          <div className="ml-auto w-fit max-w-full bg-panel-accent dark:bg-surface-dark rounded-bubble px-4 py-3 text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
            {content}
          </div>
        )}
        {/* Action buttons, branch navigator, and timestamp on hover. Skipped
            entirely while pending because none of these are valid yet. */}
        {!pending && (
        <div className="absolute -bottom-7 right-2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {formattedTimestamp && (
            <>
              <time
                dateTime={timestamp ?? undefined}
                title={`发送时间：${formattedTimestamp}`}
                className="mr-1 whitespace-nowrap text-xs leading-none tabular-nums text-text-tertiary dark:text-text-tertiary-dark"
              >
                {formattedTimestamp}
              </time>
              <div className="w-px h-3 bg-border dark:bg-border-dark mx-0.5" />
            </>
          )}
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
          {/* Rerun re-sends the text; an attachment-only message has none to
              re-send (the backend rejects blank text with no files — attachment
              replay is deliberately not a thing, the artifacts already live in
              the session inventory). Hide rather than disable: the action is
              semantically absent, not temporarily unavailable. */}
          {content.trim() !== '' && (
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
          )}
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

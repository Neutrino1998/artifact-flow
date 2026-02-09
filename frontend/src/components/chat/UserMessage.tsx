'use client';

import { memo, useState, useRef, useEffect, useCallback } from 'react';
import { useChat } from '@/hooks/useChat';
import { useStreamStore } from '@/stores/streamStore';

interface UserMessageProps {
  content: string;
  messageId: string;
  parentId: string | null;
}

function UserMessage({ content, messageId: _messageId, parentId }: UserMessageProps) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(content);
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

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

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
    await sendMessage(trimmed, parentId ?? undefined);
  }, [editContent, isStreaming, sendMessage, parentId]);

  const handleRerun = useCallback(async () => {
    if (isStreaming) return;
    await sendMessage(content, parentId ?? undefined);
  }, [content, isStreaming, sendMessage, parentId]);

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
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
          <div className="bg-surface dark:bg-surface-dark border border-accent rounded-bubble overflow-hidden">
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
              rows={1}
              className="w-full px-4 py-3 text-sm bg-transparent text-text-primary dark:text-text-primary-dark outline-none resize-none"
            />
            <div className="flex justify-end gap-2 px-3 pb-2">
              <button
                onClick={handleCancelEdit}
                className="px-3 py-1 text-xs text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmitEdit}
                disabled={!editContent.trim() || isStreaming}
                className="px-3 py-1 text-xs bg-accent text-white rounded hover:bg-accent-hover disabled:opacity-40 transition-colors"
              >
                Send
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
        <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-bubble px-4 py-3 text-sm text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
          {content}
        </div>
        {/* Action buttons on hover */}
        <div className="absolute -bottom-6 right-2 flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={handleEdit}
            disabled={isStreaming}
            className="text-xs text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark disabled:opacity-40"
            aria-label="Edit message"
          >
            Edit
          </button>
          <button
            onClick={handleRerun}
            disabled={isStreaming}
            className="text-xs text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark disabled:opacity-40"
            aria-label="Rerun message"
          >
            Rerun
          </button>
          <button
            onClick={handleCopy}
            className="text-xs text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark"
            aria-label="Copy message"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default memo(UserMessage);

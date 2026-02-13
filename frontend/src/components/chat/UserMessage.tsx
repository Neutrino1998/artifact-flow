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
    await sendMessage(trimmed, parentId);
  }, [editContent, isStreaming, sendMessage, parentId]);

  const handleRerun = useCallback(async () => {
    if (isStreaming) return;
    await sendMessage(content, parentId);
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
        <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-bubble px-4 py-3 text-sm text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
          {content}
        </div>
        {/* Action buttons on hover */}
        <div className="absolute -bottom-7 right-2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={handleEdit}
            disabled={isStreaming}
            className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
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
            className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
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
            className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            aria-label="Copy message"
            title={copied ? '已复制' : '复制'}
          >
            {copied ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export default memo(UserMessage);

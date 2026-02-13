'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { useChat } from '@/hooks/useChat';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';

export default function MessageInput() {
  const [content, setContent] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { sendMessage, disconnect, isNewConversation } = useChat();
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [content]);

  const handleSend = useCallback(async () => {
    const trimmed = content.trim();
    if (!trimmed || isStreaming) return;
    setContent('');
    await sendMessage(trimmed);
  }, [content, isStreaming, sendMessage]);

  const handleStop = useCallback(() => {
    disconnect();
  }, [disconnect]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  return (
    <div className="px-4 pt-4 pb-5">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center gap-2 bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-2xl shadow-float px-4 py-3">
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              isStreaming
                ? 'Waiting for response...'
                : isNewConversation
                  ? 'Start a new conversation...'
                  : 'Type a message...'
            }
            disabled={isStreaming}
            rows={1}
            className="flex-1 resize-none bg-transparent text-sm leading-5 text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none disabled:opacity-40 disabled:cursor-not-allowed"
          />

          {/* Artifact panel toggle */}
          <button
            onClick={toggleArtifactPanel}
            className="flex-shrink-0 p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            aria-label="Toggle artifact panel"
            title="切换文稿面板"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="1.5" y="2" width="13" height="12" rx="1.5" />
              <path d="M9.5 2v12" />
            </svg>
          </button>

          {/* Send or Stop button */}
          {isStreaming ? (
            <button
              onClick={handleStop}
              className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-500 text-white hover:bg-red-600 transition-colors"
              aria-label="Stop generation"
              title="停止生成"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <rect x="4" y="4" width="8" height="8" rx="1" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!content.trim()}
              className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-accent text-white hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              aria-label="Send message"
              title="发送消息"
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

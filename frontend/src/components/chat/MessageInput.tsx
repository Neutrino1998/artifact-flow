'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { useChat } from '@/hooks/useChat';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useStagedFilesStore } from '@/stores/stagedFilesStore';
import { injectMessage, cancelExecution } from '@/lib/api';
import { MAX_MESSAGE_CHARS, MAX_CHAT_ATTACHMENTS } from '@/lib/constants';

export default function MessageInput() {
  const [content, setContent] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const { sendMessage, isNewConversation } = useChat();
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const cancelling = useStreamStore((s) => s.cancelling);
  const setCancelling = useStreamStore((s) => s.setCancelling);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);

  const stagedFiles = useStagedFilesStore((s) => s.files);
  const addFiles = useStagedFilesStore((s) => s.addFiles);
  const removeFile = useStagedFilesStore((s) => s.removeFile);
  const clearStaged = useStagedFilesStore((s) => s.clear);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [content]);

  const conversationId = useConversationStore((s) => s.current?.id);
  const streamConversationId = useStreamStore((s) => s.conversationId);

  const handleSend = useCallback(async () => {
    if (isStreaming && !content.trim()) {
      // Stop: cancel backend execution. The cancel signal queues into the
      // engine — it only takes effect at the next checkpoint — so flip to a
      // "cancelling…" state immediately for feedback. endStream() (fired by
      // any terminal SSE event) clears it.
      if (cancelling) return;
      const convId = streamConversationId || conversationId;
      if (convId) {
        try {
          await cancelExecution(convId);
          setCancelling(true);
        } catch (err) {
          console.error('Cancel failed:', err);
        }
      }
      return;
    }

    const trimmed = content.trim();

    if (isStreaming) {
      // Inject mode: text only (attachments ride a new message, not an
      // in-flight turn). Empty inject is a no-op.
      if (!trimmed) return;
      const convId = streamConversationId || conversationId;
      if (convId) {
        try {
          await injectMessage(convId, trimmed);
          setContent('');
        } catch (err) {
          console.error('Inject failed:', err);
        }
      }
      return;
    }

    // New-message send: allow files-only (empty text + attachments). Clear the
    // composer only on success so a failed send (e.g. 422) preserves the user's
    // text and staged attachments instead of discarding them.
    const filesToSend = stagedFiles.map((s) => s.file);
    if (!trimmed && filesToSend.length === 0) return;
    const ok = await sendMessage(trimmed, undefined, filesToSend.length ? filesToSend : undefined);
    if (ok) {
      setContent('');
      clearStaged();
    }
  }, [content, isStreaming, cancelling, setCancelling, sendMessage, conversationId, streamConversationId, stagedFiles, clearStaged]);

  const handleCompositionStart = useCallback(() => {
    isComposingRef.current = true;
  }, []);

  const handleCompositionEnd = useCallback(() => {
    // Chrome fires compositionend BEFORE keydown, so delay the reset
    // to ensure the Enter keydown that confirms composition is still blocked
    requestAnimationFrame(() => {
      isComposingRef.current = false;
    });
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  // A paste larger than the message cap is diverted to a staged .txt
  // attachment instead of being inlined (which would hit the 422 cap and
  // bloat context). Smaller pastes fall through to normal insertion (capped
  // by the textarea maxLength).
  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      if (isStreaming) return;
      const text = e.clipboardData?.getData('text/plain') ?? '';
      // Divert a huge paste to a staged file only if there's room; at the
      // attachment cap, let it paste inline (textarea maxLength caps it)
      // rather than silently dropping it.
      if (text.length > MAX_MESSAGE_CHARS && stagedFiles.length < MAX_CHAT_ATTACHMENTS) {
        e.preventDefault();
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        const file = new File([text], `pasted-${ts}.txt`, { type: 'text/plain' });
        addFiles([file]);
      }
    },
    [isStreaming, addFiles, stagedFiles.length]
  );

  const handleFileSelect = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        addFiles(Array.from(files));
      }
      // Reset input so the same files can be selected again
      e.target.value = '';
    },
    [addFiles]
  );

  const atAttachmentCap = stagedFiles.length >= MAX_CHAT_ATTACHMENTS;
  const attachDisabled = isStreaming || atAttachmentCap;
  const nearLimit = content.length > MAX_MESSAGE_CHARS * 0.8;
  const hasStaged = stagedFiles.length > 0;

  return (
    <div className="relative px-4 pt-4 pb-5">
      {/* Gradient fade above input */}
      <div className="absolute inset-x-0 -top-6 h-6 bg-gradient-to-t from-chat dark:from-chat-dark to-transparent pointer-events-none" />
      <div className="max-w-3xl mx-auto">
        <div
          className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark focus-within:border-accent dark:focus-within:border-accent rounded-2xl shadow-float px-4 py-3 transition-colors"
        >
          {/* Staged attachment chips */}
          {hasStaged && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {stagedFiles.map((sf) => (
                <span
                  key={sf.id}
                  className="inline-flex items-center gap-1 max-w-[200px] pl-2 pr-1 py-1 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-xs text-text-secondary dark:text-text-secondary-dark"
                  title={sf.file.name}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                  <span className="truncate">{sf.file.name}</span>
                  <button
                    onClick={() => removeFile(sf.id)}
                    className="shrink-0 p-0.5 rounded hover:bg-surface dark:hover:bg-surface-dark text-text-tertiary dark:text-text-tertiary-dark"
                    aria-label={`Remove ${sf.file.name}`}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                      <path d="M18 6L6 18M6 6l12 12" />
                    </svg>
                  </button>
                </span>
              ))}
              <span className="inline-flex items-center px-1 text-xs tabular-nums text-text-tertiary dark:text-text-tertiary-dark">
                {stagedFiles.length}/{MAX_CHAT_ATTACHMENTS}
              </span>
            </div>
          )}

          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onCompositionStart={handleCompositionStart}
            onCompositionEnd={handleCompositionEnd}
            maxLength={MAX_MESSAGE_CHARS}
            placeholder={
              isStreaming
                ? '输入追加指令，按 Enter 发送...'
                : isNewConversation
                  ? '开始新的对话...'
                  : '输入消息...'
            }
            rows={1}
            className="w-full resize-none bg-transparent leading-5 text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
          />

          <div className="flex items-center justify-between mt-2">
            <div className="flex items-center gap-1">
              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                onChange={handleFileChange}
                className="hidden"
              />

              {/* Attach file (stages — sent with the next message) */}
              <button
                onClick={handleFileSelect}
                disabled={attachDisabled}
                className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                aria-label="Attach file"
                title={atAttachmentCap ? `最多 ${MAX_CHAT_ATTACHMENTS} 个附件` : '添加附件（随消息发送，支持多选）'}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>

              {/* Artifact panel toggle */}
              <button
                onClick={toggleArtifactPanel}
                className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
                aria-label="Toggle artifact panel"
                title="切换文稿面板"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="1.5" y="2" width="13" height="12" rx="1.5" />
                  <path d="M9.5 2v12" />
                </svg>
              </button>

              {/* Char counter — only when approaching the cap */}
              {nearLimit && (
                <span className="ml-1 text-xs tabular-nums text-text-tertiary dark:text-text-tertiary-dark">
                  {content.length}/{MAX_MESSAGE_CHARS}
                </span>
              )}
            </div>

            {/* Unified Send / Stop / Cancelling / Inject button */}
            {(() => {
              const isStop = isStreaming && !content.trim() && !cancelling;
              const sendDisabled =
                (!isStreaming && !content.trim() && !hasStaged) || cancelling;
              return (
                <button
                  onClick={handleSend}
                  disabled={sendDisabled}
                  className={`w-8 h-8 flex items-center justify-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    isStop || cancelling
                      ? 'bg-red-500 text-white hover:bg-red-600'
                      : 'bg-accent text-white hover:bg-accent-hover'
                  }`}
                  aria-label={
                    cancelling ? 'Cancelling' : isStop ? 'Stop generation' : isStreaming ? 'Inject message' : 'Send message'
                  }
                  title={cancelling ? '正在停止…' : isStop ? '停止生成' : isStreaming ? '追加指令' : '发送消息'}
                >
                  {cancelling ? (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-spin">
                      <path d="M21 12a9 9 0 1 1-6.219-8.56" strokeLinecap="round" />
                    </svg>
                  ) : isStop ? (
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                      <rect x="4" y="4" width="8" height="8" rx="1" />
                    </svg>
                  ) : (
                    <svg
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M12 19V5M5 12l7-7 7 7" />
                    </svg>
                  )}
                </button>
              );
            })()}
          </div>
        </div>
      </div>
    </div>
  );
}

'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { useChat } from '@/hooks/useChat';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { uploadFile, uploadFileNewSession, listConversations, getConversation, injectMessage, cancelExecution } from '@/lib/api';
import { useArtifacts } from '@/hooks/useArtifacts';

export default function MessageInput() {
  const [content, setContent] = useState('');
  const [uploadProgress, setUploadProgress] = useState<{ current: number; total: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const { sendMessage, isNewConversation } = useChat();
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const uploading = useArtifactStore((s) => s.uploading);
  const setUploading = useArtifactStore((s) => s.setUploading);
  const setUploadError = useArtifactStore((s) => s.setUploadError);
  const sessionId = useConversationStore((s) => s.current?.session_id);
  const { loadArtifacts, selectArtifact } = useArtifacts();

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
      // Stop: cancel backend execution
      const convId = streamConversationId || conversationId;
      if (convId) {
        try {
          await cancelExecution(convId);
        } catch (err) {
          console.error('Cancel failed:', err);
        }
      }
      return;
    }

    const trimmed = content.trim();
    if (!trimmed) return;

    if (isStreaming) {
      // Inject mode: send to active execution
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

    setContent('');
    await sendMessage(trimmed);
  }, [content, isStreaming, sendMessage, conversationId, streamConversationId]);

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

  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);

  const handleUploadFiles = useCallback(async (files: File[]) => {
    if (files.length === 0) return;

    setUploading(true);
    setUploadError(null);

    let currentSessionId = sessionId;
    let lastResultId: string | null = null;
    const total = files.length;

    try {
      for (let i = 0; i < files.length; i++) {
        if (total > 1) setUploadProgress({ current: i + 1, total });
        const file = files[i];
        let result;

        if (currentSessionId) {
          result = await uploadFile(currentSessionId, file);
        } else {
          // First file with no session — auto-create
          result = await uploadFileNewSession(file);
          currentSessionId = result.session_id;

          const [detail, list] = await Promise.all([
            getConversation(result.session_id),
            listConversations(20, 0),
          ]);
          setCurrent(detail);
          setConversations(list.conversations, list.total, list.has_more);
        }

        lastResultId = result.id;
      }

      await loadArtifacts();
      setArtifactPanelVisible(true);
      if (lastResultId) selectArtifact(lastResultId);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Upload failed';
      setUploadError(message);
      window.alert(message);
      // Refresh artifacts for any successful uploads before the error
      if (lastResultId) await loadArtifacts();
    } finally {
      setUploading(false);
      setUploadProgress(null);
    }
  }, [sessionId, setUploading, setUploadError, loadArtifacts, setArtifactPanelVisible, selectArtifact, setCurrent, setConversations]);

  const handleFileSelect = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleUploadFiles(Array.from(files));
    }
    // Reset input so the same files can be selected again
    e.target.value = '';
  }, [handleUploadFiles]);

  const uploadDisabled = uploading || isStreaming;

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!uploadDisabled) setDragOver(true);
  }, [uploadDisabled]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (uploadDisabled) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) handleUploadFiles(files);
  }, [uploadDisabled, handleUploadFiles]);

  return (
    <div className="relative px-4 pt-4 pb-5">
      {/* Gradient fade above input */}
      <div className="absolute inset-x-0 -top-6 h-6 bg-gradient-to-t from-chat dark:from-chat-dark to-transparent pointer-events-none" />
      <div className="max-w-3xl mx-auto">
        <div
          className={`bg-surface dark:bg-surface-dark border rounded-2xl shadow-float px-4 py-3 transition-colors ${
            dragOver
              ? 'border-accent ring-2 ring-accent/30'
              : 'border-border dark:border-border-dark focus-within:border-accent dark:focus-within:border-accent'
          }`}
          onDragOver={handleDragOver}
          onDragEnter={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            onCompositionStart={handleCompositionStart}
            onCompositionEnd={handleCompositionEnd}
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

              {/* Upload file */}
              <button
                onClick={handleFileSelect}
                disabled={uploadDisabled}
                className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                aria-label="Upload file"
                title="上传文件（支持多选）"
              >
                {uploading ? (
                  <span className="flex items-center gap-1">
                    <svg width="16" height="16" viewBox="0 0 16 16" className="animate-spin">
                      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" />
                    </svg>
                    {uploadProgress && (
                      <span className="text-xs tabular-nums">{uploadProgress.current}/{uploadProgress.total}</span>
                    )}
                  </span>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                )}
              </button>

              {/* Artifact panel toggle */}
              <button
                onClick={toggleArtifactPanel}
                className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
                aria-label="Toggle artifact panel"
                title="切换文稿面板"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="1.5" y="2" width="13" height="12" rx="1.5" />
                  <path d="M9.5 2v12" />
                </svg>
              </button>
            </div>

            {/* Unified Send / Stop / Inject button */}
            {(() => {
              const isStop = isStreaming && !content.trim();
              return (
                <button
                  onClick={handleSend}
                  disabled={!isStreaming && !content.trim()}
                  className={`w-8 h-8 flex items-center justify-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    isStop
                      ? 'bg-red-500 text-white hover:bg-red-600'
                      : 'bg-accent text-white hover:bg-accent-hover'
                  }`}
                  aria-label={isStop ? 'Stop generation' : isStreaming ? 'Inject message' : 'Send message'}
                  title={isStop ? '停止生成' : isStreaming ? '追加指令' : '发送消息'}
                >
                  {isStop ? (
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
                      strokeWidth="2"
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

'use client';

import { useMemo, useState, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useArtifactStore } from '@/stores/artifactStore';
import { useUIStore } from '@/stores/uiStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import { uploadFile } from '@/lib/api';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import StreamingMessage from './StreamingMessage';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 6) return '夜深了';
  if (hour < 12) return '早上好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

export default function ChatPanel() {
  const current = useConversationStore((s) => s.current);
  const currentLoading = useConversationStore((s) => s.currentLoading);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const pendingUserMessage = useStreamStore((s) => s.pendingUserMessage);

  const sessionId = useConversationStore((s) => s.current?.session_id);
  const setUploading = useArtifactStore((s) => s.setUploading);
  const setUploadError = useArtifactStore((s) => s.setUploadError);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);
  const { loadArtifacts, selectArtifact } = useArtifacts();

  const [isDragOver, setIsDragOver] = useState(false);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);

    if (!sessionId) {
      window.alert('请先发送消息创建对话');
      return;
    }

    const file = e.dataTransfer.files[0];
    if (!file) return;

    setUploading(true);
    setUploadError(null);

    try {
      const result = await uploadFile(sessionId, file);
      await loadArtifacts();
      setArtifactPanelVisible(true);
      selectArtifact(result.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Upload failed';
      setUploadError(message);
      window.alert(message);
    } finally {
      setUploading(false);
    }
  }, [sessionId, setUploading, setUploadError, loadArtifacts, setArtifactPanelVisible, selectArtifact]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    // Only set false when leaving the container (not entering children)
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragOver(false);
  }, []);

  if (currentLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-text-tertiary dark:text-text-tertiary-dark text-sm">
          加载对话中...
        </div>
      </div>
    );
  }

  // Show input even when no conversation — allows starting new conversations
  return (
    <div
      className="flex-1 flex flex-col min-h-0 relative"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {current ? (
        <MessageList />
      ) : isStreaming ? (
        // New conversation: no history yet, but stream is active
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
            {pendingUserMessage && (
              <div className="flex justify-end">
                <div className="max-w-[80%] bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-bubble px-4 py-3 text-sm text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
                  {pendingUserMessage}
                </div>
              </div>
            )}
            <StreamingMessage />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center gap-2">
          <div className="text-text-secondary dark:text-text-secondary-dark text-3xl font-semibold">
            {getGreeting()}，有什么可以帮你的？
          </div>
          <div className="text-text-tertiary dark:text-text-tertiary-dark text-sm">
            开始对话，探索更多可能
          </div>
        </div>
      )}
      <MessageInput />

      {/* Drag overlay */}
      {isDragOver && (
        <div className="absolute inset-0 bg-accent/10 border-2 border-dashed border-accent rounded-xl flex items-center justify-center z-50 pointer-events-none">
          <div className="bg-surface dark:bg-surface-dark px-6 py-4 rounded-xl shadow-float text-center">
            <div className="text-accent text-lg font-semibold">
              释放以上传文件
            </div>
            <div className="text-text-tertiary dark:text-text-tertiary-dark text-xs mt-1">
              支持文本、代码、Markdown、PDF、Word 等文档
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

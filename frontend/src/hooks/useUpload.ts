'use client';

import { useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import {
  uploadFile,
  uploadFileNewSession,
  getConversation,
  listConversations,
} from '@/lib/api';

export type UploadProgress = { current: number; total: number } | null;

interface UploadOptions {
  // Called once per file with {current, total} for multi-file uploads, then
  // null in finally. Skipped entirely on single-file uploads (no progress UI
  // for one file). Pass setUploadProgress directly.
  onProgress?: (progress: UploadProgress) => void;
}

export function useUpload() {
  const sessionId = useConversationStore((s) => s.current?.session_id);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);
  const setUploading = useArtifactStore((s) => s.setUploading);
  const setUploadError = useArtifactStore((s) => s.setUploadError);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);
  const { loadArtifacts, selectArtifact } = useArtifacts();

  return useCallback(
    async (files: File[], opts: UploadOptions = {}) => {
      if (files.length === 0) return;

      setUploading(true);
      setUploadError(null);

      let currentSessionId = sessionId;
      let lastResultId: string | null = null;
      const total = files.length;

      try {
        for (let i = 0; i < total; i++) {
          if (total > 1) opts.onProgress?.({ current: i + 1, total });
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
        opts.onProgress?.(null);
      }
    },
    [
      sessionId,
      setCurrent,
      setConversations,
      setUploading,
      setUploadError,
      setArtifactPanelVisible,
      loadArtifacts,
      selectArtifact,
    ]
  );
}

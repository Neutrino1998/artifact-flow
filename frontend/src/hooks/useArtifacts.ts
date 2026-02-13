'use client';

import { useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import * as api from '@/lib/api';

/**
 * Resolve session ID at call time.
 * During streaming for new conversations, conversation store may not have
 * session_id yet; fall back to the one stored by the SSE handler.
 */
function resolveSessionId(): string | null {
  return (
    useConversationStore.getState().current?.session_id ??
    useArtifactStore.getState().sessionId ??
    null
  );
}

export function useArtifacts() {
  const sessionId = useConversationStore((s) => s.current?.session_id);
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const setArtifactsLoading = useArtifactStore((s) => s.setArtifactsLoading);
  const setCurrent = useArtifactStore((s) => s.setCurrent);
  const setCurrentLoading = useArtifactStore((s) => s.setCurrentLoading);
  const setVersions = useArtifactStore((s) => s.setVersions);
  const setSelectedVersion = useArtifactStore((s) => s.setSelectedVersion);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  // loadArtifacts only depends on conversation store's sessionId (stable during streaming)
  const loadArtifacts = useCallback(async () => {
    if (!sessionId) return;
    setArtifactsLoading(true);
    try {
      const data = await api.listArtifacts(sessionId);
      setArtifacts(data.artifacts);
    } catch (err) {
      console.error('Failed to load artifacts:', err);
    } finally {
      setArtifactsLoading(false);
    }
  }, [sessionId, setArtifacts, setArtifactsLoading]);

  // selectArtifact resolves sessionId at call time via getState()
  const selectArtifact = useCallback(
    async (artifactId: string) => {
      const sid = resolveSessionId();
      if (!sid) return;
      setCurrentLoading(true);
      setArtifactPanelVisible(true);
      try {
        const [detail, versions] = await Promise.all([
          api.getArtifact(sid, artifactId),
          api.listVersions(sid, artifactId),
        ]);
        setCurrent(detail);
        setVersions(versions.versions);
        // Load the latest version detail (includes changes for diff view)
        const latest = versions.versions.at(-1);
        if (latest) {
          const versionDetail = await api.getVersion(sid, artifactId, latest.version);
          setSelectedVersion(versionDetail);
        } else {
          setSelectedVersion(null);
        }
      } catch (err) {
        console.error('Failed to load artifact:', err);
      } finally {
        setCurrentLoading(false);
      }
    },
    [setCurrent, setCurrentLoading, setVersions, setSelectedVersion, setArtifactPanelVisible]
  );

  const selectVersion = useCallback(
    async (artifactId: string, version: number) => {
      const sid = resolveSessionId();
      if (!sid) return;
      try {
        const detail = await api.getVersion(sid, artifactId, version);
        setSelectedVersion(detail);
      } catch (err) {
        console.error('Failed to load version:', err);
      }
    },
    [setSelectedVersion]
  );

  return { loadArtifacts, selectArtifact, selectVersion };
}

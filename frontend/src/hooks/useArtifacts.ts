'use client';

import { useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import * as api from '@/lib/api';

export function useArtifacts() {
  const sessionId = useConversationStore((s) => s.current?.session_id);
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const setArtifactsLoading = useArtifactStore((s) => s.setArtifactsLoading);
  const setCurrent = useArtifactStore((s) => s.setCurrent);
  const setCurrentLoading = useArtifactStore((s) => s.setCurrentLoading);
  const setVersions = useArtifactStore((s) => s.setVersions);
  const setSelectedVersion = useArtifactStore((s) => s.setSelectedVersion);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

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

  const selectArtifact = useCallback(
    async (artifactId: string) => {
      if (!sessionId) return;
      setCurrentLoading(true);
      setArtifactPanelVisible(true);
      try {
        const [detail, versions] = await Promise.all([
          api.getArtifact(sessionId, artifactId),
          api.listVersions(sessionId, artifactId),
        ]);
        setCurrent(detail);
        setVersions(versions.versions);
        setSelectedVersion(null);
      } catch (err) {
        console.error('Failed to load artifact:', err);
      } finally {
        setCurrentLoading(false);
      }
    },
    [sessionId, setCurrent, setCurrentLoading, setVersions, setSelectedVersion, setArtifactPanelVisible]
  );

  const selectVersion = useCallback(
    async (artifactId: string, version: number) => {
      if (!sessionId) return;
      try {
        const detail = await api.getVersion(sessionId, artifactId, version);
        setSelectedVersion(detail);
      } catch (err) {
        console.error('Failed to load version:', err);
      }
    },
    [sessionId, setSelectedVersion]
  );

  return { loadArtifacts, selectArtifact, selectVersion };
}

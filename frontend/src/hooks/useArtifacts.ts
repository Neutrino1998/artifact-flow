'use client';

import { useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import * as api from '@/lib/api';
import type { VersionSummary } from '@/types';

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

/**
 * Find the previous version number from a sorted versions list.
 * Version numbers can be sparse (e.g. 1, 3, 5) due to write-back folding.
 */
function findPrevVersion(versions: VersionSummary[], currentVersion: number): number | null {
  const sorted = versions.map((v) => v.version).sort((a, b) => a - b);
  const idx = sorted.indexOf(currentVersion);
  return idx > 0 ? sorted[idx - 1] : null;
}

export function useArtifacts() {
  const sessionId = useConversationStore((s) => s.current?.session_id);
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const setArtifactsLoading = useArtifactStore((s) => s.setArtifactsLoading);
  const setCurrent = useArtifactStore((s) => s.setCurrent);
  const setCurrentLoading = useArtifactStore((s) => s.setCurrentLoading);
  const setVersions = useArtifactStore((s) => s.setVersions);
  const setSelectedVersion = useArtifactStore((s) => s.setSelectedVersion);
  const setDiffBaseContent = useArtifactStore((s) => s.setDiffBaseContent);
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
      setArtifactPanelVisible(true);
      setCurrentLoading(true);
      try {
        const detail = await api.getArtifact(sid, artifactId);
        setCurrent(detail);
        setVersions(detail.versions);
        // current.content is already the latest — no need to fetch version detail.
        // selectedVersion is only set when user explicitly picks from the dropdown.
        setSelectedVersion(null);
        // Fetch previous version content for diff view
        const curVer = detail.current_version;
        const prevVer = curVer ? findPrevVersion(detail.versions, curVer) : null;
        if (prevVer !== null) {
          api
            .getVersion(sid, artifactId, prevVer)
            .then((base) => setDiffBaseContent(base.content))
            .catch(() => setDiffBaseContent(null));
        } else {
          setDiffBaseContent(null);
        }
      } catch (err) {
        console.error('Failed to load artifact:', err);
      } finally {
        setCurrentLoading(false);
      }
    },
    [setCurrent, setCurrentLoading, setVersions, setSelectedVersion, setDiffBaseContent, setArtifactPanelVisible]
  );

  const selectVersion = useCallback(
    async (artifactId: string, version: number) => {
      const sid = resolveSessionId();
      if (!sid) return;
      const versions = useArtifactStore.getState().versions;
      const prevVer = findPrevVersion(versions, version);
      try {
        const [detail, baseDetail] = await Promise.all([
          api.getVersion(sid, artifactId, version),
          prevVer !== null
            ? api.getVersion(sid, artifactId, prevVer)
            : Promise.resolve(null),
        ]);
        setSelectedVersion(detail);
        setDiffBaseContent(baseDetail?.content ?? null);
      } catch (err) {
        console.error('Failed to load version:', err);
      }
    },
    [setSelectedVersion, setDiffBaseContent]
  );

  return { loadArtifacts, selectArtifact, selectVersion };
}

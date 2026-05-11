'use client';

import { useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import * as api from '@/lib/api';
import { refreshArtifactList } from '@/lib/refreshArtifactList';
import { bumpArtifactDetailGen, getArtifactDetailGen } from '@/lib/artifactDetailGen';
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
  const setArtifactSessionId = useArtifactStore((s) => s.setSessionId);
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
      // Shares the same generation counter as useSSE's mid-stream / completion
      // refreshes — concurrent triggers won't race-overwrite each other.
      // refreshArtifactList stamps the artifact-store sessionId atomically.
      await refreshArtifactList(
        sessionId,
        setArtifacts,
        setArtifactSessionId,
        () => useArtifactStore.getState().sessionId,
      );
    } finally {
      setArtifactsLoading(false);
    }
  }, [sessionId, setArtifacts, setArtifactSessionId, setArtifactsLoading]);

  // selectArtifact resolves sessionId at call time via getState()
  const selectArtifact = useCallback(
    async (artifactId: string) => {
      const sid = resolveSessionId();
      if (!sid) return;
      // Bump-before-await: claim "the detail view will belong to this
      // selection". Late responses (fast A→B clicks, or any selection
      // followed by a conversation switch) check this after the await
      // and drop themselves on mismatch, so a slower A cannot overwrite
      // B's current/versions/diffBase — and a stale selection from a
      // previous conversation cannot leak into the new conversation.
      const myGen = bumpArtifactDetailGen();
      setArtifactPanelVisible(true);
      setCurrentLoading(true);
      try {
        const detail = await api.getArtifact(sid, artifactId);
        if (myGen !== getArtifactDetailGen()) return;
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
            .then((base) => {
              if (myGen !== getArtifactDetailGen()) return;
              setDiffBaseContent(base.content);
            })
            .catch(() => {
              if (myGen !== getArtifactDetailGen()) return;
              setDiffBaseContent(null);
            });
        } else {
          setDiffBaseContent(null);
        }
      } catch (err) {
        console.error('Failed to load artifact:', err);
      } finally {
        // Only clear the spinner if our selection is still the latest.
        // A stale selection's finally would otherwise prematurely clear
        // a newer selection's spinner. Conv-switch case: reset() now
        // also clears currentLoading, so a stale selection that gets
        // dropped here leaves the spinner correctly off.
        if (myGen === getArtifactDetailGen()) {
          setCurrentLoading(false);
        }
      }
    },
    [setCurrent, setCurrentLoading, setVersions, setSelectedVersion, setDiffBaseContent, setArtifactPanelVisible]
  );

  const selectVersion = useCallback(
    async (artifactId: string, version: number) => {
      const sid = resolveSessionId();
      if (!sid) return;
      // Shares the same counter as selectArtifact: a rapid v5→v3 in the
      // dropdown drops v5's slower response, and a selectArtifact firing
      // during a version fetch also invalidates the version fetch (the
      // detail view is being replaced wholesale anyway).
      const myGen = bumpArtifactDetailGen();
      const versions = useArtifactStore.getState().versions;
      const prevVer = findPrevVersion(versions, version);
      try {
        const [detail, baseDetail] = await Promise.all([
          api.getVersion(sid, artifactId, version),
          prevVer !== null
            ? api.getVersion(sid, artifactId, prevVer)
            : Promise.resolve(null),
        ]);
        if (myGen !== getArtifactDetailGen()) return;
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

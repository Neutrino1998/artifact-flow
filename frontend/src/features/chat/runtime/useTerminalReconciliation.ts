'use client';

import { useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import * as api from '@/lib/api';
import { bumpArtifactDetailGen, getArtifactDetailGen } from '@/lib/artifactDetailGen';
import { bumpArtifactFetchGen } from '@/lib/artifactFetchGen';
import { getNavGen } from '@/lib/navGen';
import { reconcileTerminalArtifact } from '@/lib/reconcileTerminalArtifact';
import { refreshArtifactList } from '@/lib/refreshArtifactList';
import { isTerminalRefreshOwner } from '@/lib/terminalRefreshOwnership';

/**
 * Owns the complete terminal reconciliation workflow for one chat stream.
 *
 * The SSE coordinator decides when a terminal arrives; this hook keeps the
 * ordered DB refresh, navigation fencing, and cross-store commits together so
 * they cannot drift into independent component effects.
 */
export function useTerminalReconciliation() {
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);
  const clearConversationActiveIfMatch = useConversationStore(
    (s) => s.clearConversationActiveIfMatch,
  );
  const applyTerminalMessageSnapshot = useConversationStore(
    (s) => s.applyTerminalMessageSnapshot,
  );

  const setArtifactSessionId = useArtifactStore((s) => s.setSessionId);
  const reconcileArtifactsFromDb = useArtifactStore((s) => s.reconcileArtifactsFromDb);
  const removeArtifactMissingFromDb = useArtifactStore((s) => s.removeArtifactMissingFromDb);
  const refreshArtifactCurrent = useArtifactStore((s) => s.refreshCurrent);
  const finishArtifactLiveTurn = useArtifactStore((s) => s.finishLiveTurn);

  const snapshotTerminalMessage = useCallback(
    (
      conversationId: string,
      messageId: string | null,
      response: string | undefined,
      metrics: unknown,
    ) => {
      if (!messageId || !response) return;
      const streamState = useStreamStore.getState();
      applyTerminalMessageSnapshot({
        conversationId,
        messageId,
        parentId: streamState.streamParentId ?? null,
        userInput: streamState.pendingUserMessage ?? '',
        response,
        executionMetrics: (metrics && typeof metrics === 'object')
          ? metrics as Record<string, unknown>
          : null,
        uploadedFiles: streamState.pendingUserFiles?.map((filename) => ({ filename })) ?? null,
        activatedSkills: streamState.pendingUserSkills,
      });
    },
    [applyTerminalMessageSnapshot],
  );

  const refreshAfterComplete = useCallback(
    async (conversationId: string, terminalMessageId: string | null) => {
      // Navigation generation is the authority for whether an async terminal
      // refresh may still write into the currently displayed conversation.
      const myNavGen = getNavGen();
      if (!isTerminalRefreshOwner(myNavGen, terminalMessageId)) {
        if (terminalMessageId) {
          clearConversationActiveIfMatch(conversationId, terminalMessageId);
        }
        return;
      }

      const myArtifactDetailGen = bumpArtifactDetailGen();
      const ownsTerminalRefresh = () =>
        isTerminalRefreshOwner(myNavGen, terminalMessageId);
      const ownsArtifactRefresh = () =>
        ownsTerminalRefresh() && myArtifactDetailGen === getArtifactDetailGen();

      // End the optimistic live window before any await. In-flight detail and
      // auto-open reads from the live turn are fenced by their generations.
      bumpArtifactFetchGen();
      finishArtifactLiveTurn();

      // Artifact persistence reconciliation is independent from the secondary
      // conversation/sidebar refresh and therefore starts immediately.
      const artifactSession = useArtifactStore.getState().sessionId;
      if (artifactSession === conversationId) {
        void refreshArtifactList(
          conversationId,
          reconcileArtifactsFromDb,
          setArtifactSessionId,
          () => useArtifactStore.getState().sessionId,
          ownsTerminalRefresh,
        );

        const { current: currentArtifact } = useArtifactStore.getState();
        if (currentArtifact && ownsArtifactRefresh()) {
          void reconcileTerminalArtifact({
            sessionId: conversationId,
            artifactId: currentArtifact.id,
            isOwner: ownsTerminalRefresh,
            commitPresent: (artifact, diffBaseContent) => {
              if (ownsArtifactRefresh()) {
                refreshArtifactCurrent(artifact, diffBaseContent);
              }
            },
            // Missing is collection truth; only terminal ownership, not a
            // later detail selection, determines whether it may be committed.
            commitMissing: (artifactId) => {
              if (ownsTerminalRefresh()) {
                removeArtifactMissingFromDb(artifactId);
              }
            },
          });
        }
      }

      // Compare-and-clear only this terminal's optimistic sidebar marker. A
      // newer turn on the same conversation keeps its own message id.
      if (terminalMessageId) {
        clearConversationActiveIfMatch(conversationId, terminalMessageId);
      }

      try {
        const [detail, list] = await Promise.all([
          api.getConversation(conversationId, { force: true }),
          api.listConversations(20, 0),
        ]);

        // The list is an authoritative snapshot, but the immediate CAS also
        // handles the server-side lease-release window after a terminal event.
        setConversations(list.conversations, list.total, list.has_more);
        if (terminalMessageId) {
          clearConversationActiveIfMatch(conversationId, terminalMessageId);
        }

        if (!ownsTerminalRefresh()) return;
        setCurrent(detail);
      } catch (err) {
        console.error('Failed to refresh after complete:', err);
      }
    },
    [
      clearConversationActiveIfMatch,
      finishArtifactLiveTurn,
      reconcileArtifactsFromDb,
      refreshArtifactCurrent,
      removeArtifactMissingFromDb,
      setArtifactSessionId,
      setConversations,
      setCurrent,
    ],
  );

  return { snapshotTerminalMessage, refreshAfterComplete };
}

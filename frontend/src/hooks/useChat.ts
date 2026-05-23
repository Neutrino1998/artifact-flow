'use client';

import { useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStagedFilesStore } from '@/stores/stagedFilesStore';
import { useUIStore } from '@/stores/uiStore';
import { useSSE } from '@/hooks/useSSE';
import type { ChatRequest } from '@/types';
import * as api from '@/lib/api';
import { getNavGen, bumpNavGen } from '@/lib/navGen';
import { bumpArtifactFetchGen } from '@/lib/artifactFetchGen';
import { bumpArtifactDetailGen } from '@/lib/artifactDetailGen';
import { refreshArtifactList } from '@/lib/refreshArtifactList';

export function useChat() {
  const current = useConversationStore((s) => s.current);
  const branchPath = useConversationStore((s) => s.branchPath);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setCurrentLoading = useConversationStore((s) => s.setCurrentLoading);
  const setConversations = useConversationStore((s) => s.setConversations);
  const setConversationActiveMessage = useConversationStore((s) => s.setConversationActiveMessage);
  const setPendingUserMessage = useStreamStore((s) => s.setPendingUserMessage);
  const setStreamParentId = useStreamStore((s) => s.setStreamParentId);
  const setError = useStreamStore((s) => s.setError);
  const resetStream = useStreamStore((s) => s.reset);
  const resetArtifacts = useArtifactStore((s) => s.reset);
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const setArtifactSessionId = useArtifactStore((s) => s.setSessionId);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);
  const { connect, disconnect, reconnectIfActive } = useSSE();

  const isNewConversation = !current;

  // Get the last message in current branch path for parent_message_id
  const lastMessageId = branchPath.length > 0 ? branchPath[branchPath.length - 1].id : null;

  const sendMessage = useCallback(
    async (content: string, parentMessageId?: string | null, files?: File[]) => {
      // Capture nav-gen BEFORE the await. If the user clicks New Chat or
      // switches to another conversation while api.sendMessage() is in
      // flight, the engine still runs server-side (runner.submit is
      // fire-and-forget — the turn will complete, write events, persist
      // artifacts); we just must not redirect the abandoned response into
      // the new UI context. reconnectIfActive() reattaches if the user
      // returns. Without this guard, pendingUserMessage/connect would
      // flip streamStore into the abandoned conversation, and Stop/Inject
      // in MessageInput (keyed off global isStreaming + streamConversationId)
      // would target the wrong turn.
      const myNavGen = getNavGen();
      try {
        // undefined = use default (last message in branch), omit from body
        // null = explicitly no parent (create new root), send as null
        // string = explicit parent ID
        const body: ChatRequest = {
          user_input: content,
          conversation_id: current?.id ?? undefined,
        };

        if (parentMessageId === undefined) {
          // Default: use last message in current branch
          if (lastMessageId) body.parent_message_id = lastMessageId;
        } else {
          // Explicit: null (root) or string (specific parent)
          body.parent_message_id = parentMessageId;
        }

        const isNew = !current?.id;
        const res = await api.sendMessage(body, files);

        // Sidebar refresh fires BEFORE the nav-gen check on purpose.
        // The server has created the conversation regardless of whether
        // the user navigated away during the request; if we gate this
        // behind the nav-gen guard, the abandoned-but-running conv
        // becomes unreachable from the sidebar until a manual refresh.
        // The write itself is cross-conv safe (only touches the global
        // sidebar list, no per-conv UI state).
        if (isNew) {
          api.listConversations(20, 0).then((data) => {
            setConversations(data.conversations, data.total, data.has_more);
          });
        } else {
          // Existing conv: optimistically write the cached active_message_id
          // so the sidebar dot lights up without waiting for the next list
          // refresh. Cleared by refreshAfterComplete via compare-and-clear:
          // only the terminal carrying THIS message_id can clear it, so a
          // late terminal from a previous turn cannot wipe out this mark.
          // For new convs the list refresh above already brings the field
          // from the backend response, so no double-write needed.
          setConversationActiveMessage(res.conversation_id, res.message_id);
        }

        if (myNavGen !== getNavGen()) return;

        setPendingUserMessage(content);
        // Track rerun/edit parent for branchPath truncation
        if (parentMessageId !== undefined) {
          setStreamParentId(parentMessageId);
        }

        // connect() now also flips streamStore into streaming state, so
        // sendMessage no longer needs to call startStream itself.
        connect(res.stream_url, res.conversation_id, res.message_id);

        // Attachments became user_upload artifacts server-side before the turn
        // started; surface them in the panel now — the SSE stream won't
        // re-emit pre-created artifacts. Mirrors the prior upload UX.
        if (files && files.length > 0) {
          refreshArtifactList(
            res.conversation_id,
            setArtifacts,
            setArtifactSessionId,
            () => useArtifactStore.getState().sessionId,
          );
          setArtifactPanelVisible(true);
        }
      } catch (err) {
        if (myNavGen !== getNavGen()) return;
        setError((err as Error).message);
      }
    },
    [current?.id, lastMessageId, setPendingUserMessage, setStreamParentId, connect, setError, setConversations, setConversationActiveMessage, setArtifacts, setArtifactSessionId, setArtifactPanelVisible]
  );

  // Switch to an existing conversation: tear down the previous conversation's
  // SSE + in-flight stream/artifact state, load the new conversation's detail,
  // then re-attach to the live tail if backend execution is still active.
  // Centralized here so all entry points (sidebar list, search browser) use
  // the same lifecycle and we don't accumulate background SSE connections.
  const switchConversation = useCallback(
    async (id: string) => {
      if (id === current?.id) return;
      const myGen = bumpNavGen();
      // Also invalidate any in-flight artifact auto-open fetches from the
      // previous conversation. resetArtifacts() below clears `current` to
      // null, which would otherwise let a late getArtifact response sail
      // through autoOpenArtifact's `cur==null` branch and inject an
      // artifact from the abandoned session into the new one.
      bumpArtifactFetchGen();
      // Symmetric for manual selectArtifact / selectVersion in flight:
      // their post-await writes (setCurrent / setVersions / setDiffBase)
      // would otherwise leak into the new conversation's panel.
      bumpArtifactDetailGen();
      disconnect();
      resetStream();
      resetArtifacts();
      useStagedFilesStore.getState().clear();  // composer attachments don't carry across conversations
      setCurrentLoading(true);
      // Fire-and-forget sidebar refresh: the previous conv's SSE was just
      // disconnected, so any terminal events emitted while we're away will
      // not reach this tab — the cached active_message_id for that conv
      // would stay stuck. Re-fetching the list on every nav is the cheap
      // recovery path (covers cross-tab/device too) — the backend lease
      // store is authoritative, so the refresh restores truth. Gated by
      // nav-gen so a stale response can't overwrite a newer switch's data.
      api.listConversations(20, 0).then((data) => {
        if (myGen === getNavGen()) {
          setConversations(data.conversations, data.total, data.has_more);
        }
      }).catch(() => {});
      try {
        const detail = await api.getConversation(id);
        // A later switch (or startNewChat) bumped the counter while we were
        // awaiting — our setCurrent/reconnect would clobber that newer
        // selection. Bail. setCurrentLoading is also gated on myGen so we
        // don't race against the latest switch's loading flag.
        if (myGen !== getNavGen()) return;
        setCurrent(detail);
        reconnectIfActive(id);
        // Auto-open the artifact panel if this conversation has artifacts.
        // Fire-and-forget so a slow / large artifact list does not delay
        // clearing `currentLoading` (the chat panel must surface as soon
        // as the conversation detail is in). refreshArtifactList carries
        // its own gen + session-stamp guard, so ArtifactPanel's mount
        // effect (if already visible) and this call settle latest-wins.
        //
        // Snapshot rightPanelIntentEpoch BEFORE the probe and bail in the
        // callback if it moved — any user-driven right-panel intent change
        // in the interim (artifact toggle, user-mgmt open/close, or
        // observability open/close) means the user has expressed intent
        // and our late auto-open must not override it. Plain
        // `artifactPanelVisible` snapshot would miss (a) toggled-and-back
        // because final value matches initial and (b) sibling panels
        // (user-mgmt / observability) re-targeting the right panel.
        const epochBefore = useUIStore.getState().rightPanelIntentEpoch;
        refreshArtifactList(
          detail.session_id,
          setArtifacts,
          setArtifactSessionId,
          () => useArtifactStore.getState().sessionId,
        ).then(() => {
          if (myGen !== getNavGen()) return;
          if (useUIStore.getState().rightPanelIntentEpoch !== epochBefore) return;
          if (useUIStore.getState().artifactPanelVisible) return;
          if (useArtifactStore.getState().artifacts.length === 0) return;
          setArtifactPanelVisible(true);
        });
      } catch (err) {
        if (myGen !== getNavGen()) return;
        console.error('Failed to load conversation:', err);
      } finally {
        if (myGen === getNavGen()) {
          setCurrentLoading(false);
        }
      }
    },
    [current?.id, disconnect, resetStream, resetArtifacts, setCurrentLoading, setCurrent, setConversations, reconnectIfActive, setArtifacts, setArtifactSessionId, setArtifactPanelVisible]
  );

  // Drop into the new-conversation flow: same teardown as switchConversation
  // but no detail to load, current goes to null. Bumping nav-gen here
  // invalidates any in-flight switchConversation (its late getConversation
  // response can't rewrite current back to a stale selection) AND any
  // in-flight useSSE.refreshAfterComplete (its late setCurrent(detail) can't
  // revive an abandoned conversation). We also clear currentLoading
  // explicitly: an invalidated switchConversation will skip its
  // `setCurrentLoading(false)` in finally (gen mismatch), and the new chat
  // semantically has nothing loading, so this is the right place to ensure
  // the chat panel doesn't get stuck on the loading placeholder.
  const startNewChat = useCallback(() => {
    bumpNavGen();
    // Symmetric with switchConversation: a late artifact auto-open from the
    // abandoned conversation must not leak its artifact into the empty
    // new-chat panel.
    bumpArtifactFetchGen();
    // Likewise for in-flight manual selectArtifact / selectVersion.
    bumpArtifactDetailGen();
    disconnect();
    resetStream();
    resetArtifacts();
    useStagedFilesStore.getState().clear();  // composer attachments don't carry into a new chat
    setCurrent(null);
    setCurrentLoading(false);
  }, [disconnect, resetStream, resetArtifacts, setCurrent, setCurrentLoading]);

  const refreshConversation = useCallback(
    async (conversationId: string) => {
      try {
        const detail = await api.getConversation(conversationId);
        setCurrent(detail);
      } catch (err) {
        console.error('Failed to refresh conversation:', err);
      }
    },
    [setCurrent]
  );

  const refreshConversationList = useCallback(async () => {
    try {
      const data = await api.listConversations(20, 0);
      setConversations(data.conversations, data.total, data.has_more);
    } catch (err) {
      console.error('Failed to refresh conversations list:', err);
    }
  }, [setConversations]);

  return {
    sendMessage,
    switchConversation,
    startNewChat,
    disconnect,
    refreshConversation,
    refreshConversationList,
    isNewConversation,
  };
}

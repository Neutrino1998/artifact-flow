'use client';

import { useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useArtifactStore } from '@/stores/artifactStore';
import { useSSE } from '@/hooks/useSSE';
import type { ChatRequest } from '@/types';
import * as api from '@/lib/api';

// Module-level monotonic counter shared across all useChat() instances.
// switchConversation captures the value at entry; if a later switch (from
// any component, including startNewChat) bumps the counter while we're
// awaiting getConversation, the late response is dropped instead of
// rewriting current/SSE on top of the user's newer selection.
let _switchGen = 0;

export function useChat() {
  const current = useConversationStore((s) => s.current);
  const branchPath = useConversationStore((s) => s.branchPath);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setCurrentLoading = useConversationStore((s) => s.setCurrentLoading);
  const setConversations = useConversationStore((s) => s.setConversations);
  const setPendingUserMessage = useStreamStore((s) => s.setPendingUserMessage);
  const setStreamParentId = useStreamStore((s) => s.setStreamParentId);
  const setError = useStreamStore((s) => s.setError);
  const resetStream = useStreamStore((s) => s.reset);
  const resetArtifacts = useArtifactStore((s) => s.reset);
  const { connect, disconnect, reconnectIfActive } = useSSE();

  const isNewConversation = !current;

  // Get the last message in current branch path for parent_message_id
  const lastMessageId = branchPath.length > 0 ? branchPath[branchPath.length - 1].id : null;

  const sendMessage = useCallback(
    async (content: string, parentMessageId?: string | null) => {
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
        const res = await api.sendMessage(body);
        setPendingUserMessage(content);
        // Track rerun/edit parent for branchPath truncation
        if (parentMessageId !== undefined) {
          setStreamParentId(parentMessageId);
        }

        // connect() now also flips streamStore into streaming state, so
        // sendMessage no longer needs to call startStream itself.
        connect(res.stream_url, res.conversation_id, res.message_id);

        // Refresh sidebar immediately so the new conversation appears
        if (isNew) {
          api.listConversations(20, 0).then((data) => {
            setConversations(data.conversations, data.total, data.has_more);
          });
        }
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [current?.id, lastMessageId, setPendingUserMessage, setStreamParentId, connect, setError]
  );

  // Switch to an existing conversation: tear down the previous conversation's
  // SSE + in-flight stream/artifact state, load the new conversation's detail,
  // then re-attach to the live tail if backend execution is still active.
  // Centralized here so all entry points (sidebar list, search browser) use
  // the same lifecycle and we don't accumulate background SSE connections.
  const switchConversation = useCallback(
    async (id: string) => {
      if (id === current?.id) return;
      const myGen = ++_switchGen;
      disconnect();
      resetStream();
      resetArtifacts();
      setCurrentLoading(true);
      try {
        const detail = await api.getConversation(id);
        // A later switch (or startNewChat) bumped the counter while we were
        // awaiting — our setCurrent/reconnect would clobber that newer
        // selection. Bail. setCurrentLoading is also gated on myGen so we
        // don't race against the latest switch's loading flag.
        if (myGen !== _switchGen) return;
        setCurrent(detail);
        reconnectIfActive(id);
      } catch (err) {
        if (myGen !== _switchGen) return;
        console.error('Failed to load conversation:', err);
      } finally {
        if (myGen === _switchGen) {
          setCurrentLoading(false);
        }
      }
    },
    [current?.id, disconnect, resetStream, resetArtifacts, setCurrentLoading, setCurrent, reconnectIfActive]
  );

  // Drop into the new-conversation flow: same teardown as switchConversation
  // but no detail to load, current goes to null. Bumping _switchGen here
  // invalidates any in-flight switchConversation so its late getConversation
  // response can't rewrite current back to a stale selection. We also clear
  // currentLoading explicitly: an invalidated switchConversation will skip
  // its `setCurrentLoading(false)` in finally (gen mismatch), and the new
  // chat semantically has nothing loading, so this is the right place to
  // ensure the chat panel doesn't get stuck on the loading placeholder.
  const startNewChat = useCallback(() => {
    _switchGen++;
    disconnect();
    resetStream();
    resetArtifacts();
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

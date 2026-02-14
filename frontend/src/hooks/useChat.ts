'use client';

import { useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useSSE } from '@/hooks/useSSE';
import type { ChatRequest } from '@/types';
import * as api from '@/lib/api';

export function useChat() {
  const current = useConversationStore((s) => s.current);
  const branchPath = useConversationStore((s) => s.branchPath);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);
  const startStream = useStreamStore((s) => s.startStream);
  const setPendingUserMessage = useStreamStore((s) => s.setPendingUserMessage);
  const setStreamParentId = useStreamStore((s) => s.setStreamParentId);
  const setError = useStreamStore((s) => s.setError);
  const { connect, disconnect } = useSSE();

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
          content,
          conversation_id: current?.id ?? undefined,
        };

        if (parentMessageId === undefined) {
          // Default: use last message in current branch
          if (lastMessageId) body.parent_message_id = lastMessageId;
        } else {
          // Explicit: null (root) or string (specific parent)
          body.parent_message_id = parentMessageId;
        }

        const res = await api.sendMessage(body);
        setPendingUserMessage(content);
        // Track rerun/edit parent for branchPath truncation
        if (parentMessageId !== undefined) {
          setStreamParentId(parentMessageId);
        }
        startStream(res.stream_url, res.thread_id, res.message_id, res.conversation_id);

        // Connect SSE for streaming
        connect(res.stream_url, res.conversation_id, res.message_id);
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [current?.id, lastMessageId, startStream, setPendingUserMessage, setStreamParentId, connect, setError]
  );

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
    disconnect,
    refreshConversation,
    refreshConversationList,
    isNewConversation,
  };
}

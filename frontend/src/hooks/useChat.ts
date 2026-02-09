'use client';

import { useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useSSE } from '@/hooks/useSSE';
import * as api from '@/lib/api';

export function useChat() {
  const current = useConversationStore((s) => s.current);
  const branchPath = useConversationStore((s) => s.branchPath);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);
  const startStream = useStreamStore((s) => s.startStream);
  const setError = useStreamStore((s) => s.setError);
  const { connect, disconnect } = useSSE();

  const isNewConversation = !current;

  // Get the last message in current branch path for parent_message_id
  const lastMessageId = branchPath.length > 0 ? branchPath[branchPath.length - 1].id : null;

  const sendMessage = useCallback(
    async (content: string, parentMessageId?: string) => {
      try {
        const body = {
          content,
          conversation_id: current?.id ?? undefined,
          parent_message_id: parentMessageId ?? lastMessageId ?? undefined,
        };

        const res = await api.sendMessage(body);
        startStream(res.stream_url, res.thread_id, res.message_id);

        // Connect SSE for streaming
        connect(res.stream_url, res.conversation_id, res.message_id);
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [current?.id, lastMessageId, startStream, connect, setError]
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

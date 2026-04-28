'use client';

import { useEffect, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import { useChat } from '@/hooks/useChat';
import { listConversations } from '@/lib/api';
import ConversationItem from './ConversationItem';

export default function ConversationList() {
  const conversations = useConversationStore((s) => s.conversations);
  const hasMore = useConversationStore((s) => s.hasMore);
  const listLoading = useConversationStore((s) => s.listLoading);
  const currentId = useConversationStore((s) => s.current?.id);
  const setConversations = useConversationStore((s) => s.setConversations);
  const setListLoading = useConversationStore((s) => s.setListLoading);
  const setConversationBrowserVisible = useUIStore((s) => s.setConversationBrowserVisible);
  const setUserManagementVisible = useUIStore((s) => s.setUserManagementVisible);
  const { switchConversation } = useChat();

  const loadConversations = useCallback(async () => {
    setListLoading(true);
    try {
      const data = await listConversations(20, 0);
      setConversations(data.conversations, data.total, data.has_more);
    } catch (err) {
      console.error('Failed to load conversations:', err);
    } finally {
      setListLoading(false);
    }
  }, [setConversations, setListLoading]);

  const selectConversation = useCallback(
    async (id: string) => {
      setConversationBrowserVisible(false);
      setUserManagementVisible(false);
      await switchConversation(id);
    },
    [switchConversation, setConversationBrowserVisible, setUserManagementVisible]
  );

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  return (
    <div className="flex-1 overflow-y-auto">
      {conversations.map((conv) => (
        <ConversationItem
          key={conv.id}
          conversation={conv}
          isActive={conv.id === currentId}
          onSelect={selectConversation}
        />
      ))}

      {listLoading && (
        <div className="px-4 py-3 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
          Loading...
        </div>
      )}

      {hasMore && !listLoading && (
        <div className="mx-2 mb-1">
          <button
            onClick={() => setConversationBrowserVisible(true)}
            className="w-full px-3 py-2 text-xs text-text-secondary dark:text-text-secondary-dark rounded-lg hover:bg-chat/60 dark:hover:bg-panel-accent-dark/60 transition-colors"
          >
            显示所有对话
          </button>
        </div>
      )}

      {!listLoading && conversations.length === 0 && (
        <div className="px-4 py-8 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
          No conversations yet
        </div>
      )}
    </div>
  );
}

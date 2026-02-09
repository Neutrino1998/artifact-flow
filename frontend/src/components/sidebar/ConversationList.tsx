'use client';

import { useEffect, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { listConversations, getConversation } from '@/lib/api';
import ConversationItem from './ConversationItem';

export default function ConversationList() {
  const conversations = useConversationStore((s) => s.conversations);
  const hasMore = useConversationStore((s) => s.hasMore);
  const listLoading = useConversationStore((s) => s.listLoading);
  const currentId = useConversationStore((s) => s.current?.id);
  const setConversations = useConversationStore((s) => s.setConversations);
  const appendConversations = useConversationStore((s) => s.appendConversations);
  const setListLoading = useConversationStore((s) => s.setListLoading);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setCurrentLoading = useConversationStore((s) => s.setCurrentLoading);

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

  const loadMore = useCallback(async () => {
    if (listLoading || !hasMore) return;
    setListLoading(true);
    try {
      const data = await listConversations(20, conversations.length);
      appendConversations(data.conversations, data.total, data.has_more);
    } catch (err) {
      console.error('Failed to load more conversations:', err);
    } finally {
      setListLoading(false);
    }
  }, [listLoading, hasMore, conversations.length, appendConversations, setListLoading]);

  const selectConversation = useCallback(
    async (id: string) => {
      if (id === currentId) return;
      setCurrentLoading(true);
      try {
        const detail = await getConversation(id);
        setCurrent(detail);
      } catch (err) {
        console.error('Failed to load conversation:', err);
      } finally {
        setCurrentLoading(false);
      }
    },
    [currentId, setCurrent, setCurrentLoading]
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
        <button
          onClick={loadMore}
          className="w-full px-4 py-2 text-xs text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
        >
          Load more
        </button>
      )}

      {!listLoading && conversations.length === 0 && (
        <div className="px-4 py-8 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
          No conversations yet
        </div>
      )}
    </div>
  );
}

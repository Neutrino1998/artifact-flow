'use client';

import { useEffect, useCallback, useState } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { listAdminConversations } from '@/lib/api';
import type { AdminConversationSummary } from '@/lib/api';
import { parseUtcIso } from '@/lib/time';
import { MENU_ROW_HOVER } from '@/lib/styles';

export default function AdminConversationList({
  onNavigate = () => {},
}: {
  onNavigate?: () => void;
}) {
  const [conversations, setConversations] = useState<AdminConversationSummary[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const selectedConvId = useUIStore((s) => s.observabilitySelectedConvId);
  const setSelectedConvId = useUIStore((s) => s.setObservabilitySelectedConvId);
  const setObservabilityBrowser = useUIStore((s) => s.setObservabilityBrowser);
  const refreshTick = useUIStore((s) => s.observabilityRefreshTick);

  const loadConversations = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listAdminConversations(20, 0);
      setConversations(data.conversations);
      setHasMore(data.has_more);
    } catch (err) {
      console.error('Failed to load admin conversations:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations, refreshTick]);

  const handleSelectConversation = useCallback((conversationId: string) => {
    setSelectedConvId(conversationId);
    onNavigate();
  }, [onNavigate, setSelectedConvId]);

  const handleShowAll = useCallback(() => {
    setObservabilityBrowser('conversations');
    onNavigate();
  }, [onNavigate, setObservabilityBrowser]);

  return (
    <div className="flex-1 overflow-y-auto">
      {conversations.map((conv) => (
        <div
          key={conv.id}
          className={`group relative cursor-pointer transition-colors rounded-lg mx-2 px-3 py-2.5 ${
            conv.id === selectedConvId
              ? 'bg-chat dark:bg-panel-accent-dark'
              : MENU_ROW_HOVER
          }`}
          onClick={() => handleSelectConversation(conv.id)}
        >
          <div className="flex items-center gap-1.5">
            {conv.is_active && (
              <span className="inline-block w-2 h-2 rounded-full bg-status-running flex-shrink-0" title="运行中" />
            )}
            <span className="font-medium truncate text-text-primary dark:text-text-primary-dark">
              {conv.title || 'Untitled'}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
            <span>{conv.user_display_name || conv.user_id || '-'}</span>
            <span>{conv.message_count} msgs</span>
            <span>{parseUtcIso(conv.updated_at).toLocaleDateString()}</span>
          </div>
        </div>
      ))}

      {loading && (
        <div className="px-4 py-3 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
          Loading...
        </div>
      )}

      {hasMore && !loading && (
        <div className="mx-2 mb-1">
          <button
            onClick={handleShowAll}
            className={`w-full px-3 py-2 text-xs text-text-secondary dark:text-text-secondary-dark rounded-lg ${MENU_ROW_HOVER}`}
          >
            显示所有对话
          </button>
        </div>
      )}

      {!loading && conversations.length === 0 && (
        <div className="px-4 py-8 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
          暂无对话
        </div>
      )}
    </div>
  );
}

'use client';

import { useEffect, useCallback, useState } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { listAdminConversations } from '@/lib/api';
import type { AdminConversationSummary } from '@/lib/api';

export default function AdminConversationList() {
  const [conversations, setConversations] = useState<AdminConversationSummary[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const selectedConvId = useUIStore((s) => s.observabilitySelectedConvId);
  const setSelectedConvId = useUIStore((s) => s.setObservabilitySelectedConvId);
  const setObservabilityBrowseVisible = useUIStore((s) => s.setObservabilityBrowseVisible);
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

  return (
    <div className="flex-1 overflow-y-auto">
      {conversations.map((conv) => (
        <div
          key={conv.id}
          className={`group relative cursor-pointer transition-colors rounded-lg mx-2 px-3 py-2.5 ${
            conv.id === selectedConvId
              ? 'bg-chat dark:bg-panel-accent-dark'
              : 'hover:bg-chat/60 dark:hover:bg-panel-accent-dark/60'
          }`}
          onClick={() => setSelectedConvId(conv.id)}
        >
          <div className="flex items-center gap-1.5">
            {conv.is_active && (
              <span className="inline-block w-2 h-2 rounded-full bg-green-500 flex-shrink-0" title="活跃执行中" />
            )}
            <span className="font-medium truncate text-text-primary dark:text-text-primary-dark">
              {conv.title || 'Untitled'}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
            <span>{conv.user_display_name || conv.user_id || '-'}</span>
            <span>{conv.message_count} msgs</span>
            <span>{new Date(conv.updated_at).toLocaleDateString()}</span>
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
            onClick={() => setObservabilityBrowseVisible(true)}
            className="w-full px-3 py-2 text-xs text-text-secondary dark:text-text-secondary-dark rounded-lg hover:bg-chat/60 dark:hover:bg-panel-accent-dark/60 transition-colors"
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

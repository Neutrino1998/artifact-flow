'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import { listConversations, getConversation, deleteConversation } from '@/lib/api';
import type { ConversationSummary } from '@/types';
import ConfirmModal from '@/components/layout/ConfirmModal';

const PAGE_SIZE = 20;

export default function ConversationBrowser() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const currentId = useConversationStore((s) => s.current?.id);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setCurrentLoading = useConversationStore((s) => s.setCurrentLoading);
  const removeConversation = useConversationStore((s) => s.removeConversation);
  const setConversationBrowserVisible = useUIStore((s) => s.setConversationBrowserVisible);

  const fetchConversations = useCallback(async (searchQuery: string, offset = 0, append = false) => {
    setLoading(true);
    try {
      const trimmed = searchQuery.trim() || undefined;
      const data = await listConversations(PAGE_SIZE, offset, trimmed);
      if (append) {
        setConversations((prev) => [...prev, ...data.conversations]);
      } else {
        setConversations(data.conversations);
      }
      setTotal(data.total);
      setHasMore(data.has_more);
    } catch (err) {
      console.error('Failed to load conversations:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchConversations('');
  }, [fetchConversations]);

  // Debounced search
  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchConversations(value);
    }, 300);
  }, [fetchConversations]);

  const handleLoadMore = useCallback(() => {
    if (loading || !hasMore) return;
    fetchConversations(query, conversations.length, true);
  }, [loading, hasMore, query, conversations.length, fetchConversations]);

  const handleSelect = useCallback(async (id: string) => {
    setCurrentLoading(true);
    try {
      const detail = await getConversation(id);
      setCurrent(detail);
      setConversationBrowserVisible(false);
    } catch (err) {
      console.error('Failed to load conversation:', err);
    } finally {
      setCurrentLoading(false);
    }
  }, [setCurrent, setCurrentLoading, setConversationBrowserVisible]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      setTotal((prev) => prev - 1);
      removeConversation(id);
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  }, [removeConversation]);

  const handleClose = useCallback(() => {
    setConversationBrowserVisible(false);
  }, [setConversationBrowserVisible]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      {/* Search */}
      <div className="px-4 pt-4 pb-2">
        <div className="max-w-3xl mx-auto">
          <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark focus-within:border-accent dark:focus-within:border-accent rounded-2xl shadow-float px-4 py-3 flex items-center gap-3">
            <svg
              className="flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark"
              width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
            >
              <circle cx="7" cy="7" r="5" />
              <path d="M11 11l3.5 3.5" />
            </svg>
            <input
              type="text"
              value={query}
              onChange={(e) => handleQueryChange(e.target.value)}
              placeholder="搜索对话标题..."
              autoFocus
              className="flex-1 bg-transparent text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
            />
            <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              {total} 对话
            </span>
            <button
              onClick={handleClose}
              className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
              aria-label="关闭"
              title="关闭"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
        {conversations.map((conv) => (
          <BrowserItem
            key={conv.id}
            conversation={conv}
            isActive={conv.id === currentId}
            onSelect={handleSelect}
            onDelete={handleDelete}
          />
        ))}

        {loading && (
          <div className="py-4 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            Loading...
          </div>
        )}

        {hasMore && !loading && (
          <button
            onClick={handleLoadMore}
            className="w-full py-2.5 text-sm text-text-secondary dark:text-text-secondary-dark rounded-lg hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60 transition-colors"
          >
            显示更多
          </button>
        )}

        {!loading && conversations.length === 0 && (
          <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
            {query ? '没有找到匹配的对话' : '暂无对话'}
          </div>
        )}
        </div>
      </div>
    </div>
  );
}

function BrowserItem({
  conversation,
  isActive,
  onSelect,
  onDelete,
}: {
  conversation: ConversationSummary;
  isActive: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [showMenu, setShowMenu] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const title = conversation.title || 'Untitled';
  const date = new Date(conversation.updated_at).toLocaleDateString();

  return (
    <>
      <div
        className={`group relative cursor-pointer transition-colors rounded-lg mb-1 ${
          isActive
            ? 'bg-panel dark:bg-panel-accent-dark px-4 py-3'
            : 'hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60 px-4 py-3'
        }`}
        onClick={() => onSelect(conversation.id)}
        onMouseEnter={() => setShowMenu(true)}
        onMouseLeave={() => setShowMenu(false)}
      >
        <div className={`font-medium text-text-primary dark:text-text-primary-dark truncate ${showMenu ? 'pr-8' : ''}`}>
          {title}
        </div>
        <div className="flex items-center gap-2 mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          <span>{date}</span>
          <span>{conversation.message_count} messages</span>
        </div>

        {showMenu && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setConfirmDelete(true);
            }}
            className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error dark:hover:text-status-error hover:bg-status-error/10 dark:hover:bg-status-error/10 transition-colors"
            aria-label="删除对话"
            title="删除对话"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
            </svg>
          </button>
        )}
      </div>

      {confirmDelete && (
        <ConfirmModal
          title="删除对话"
          message={`确定要删除对话「${title}」吗？此操作无法撤销。`}
          confirmLabel="删除"
          destructive
          onConfirm={() => {
            onDelete(conversation.id);
            setConfirmDelete(false);
          }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </>
  );
}

'use client';

import { useState, useCallback, useEffect, useRef, } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import { useChat } from '@/hooks/useChat';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { listConversations, deleteConversation, bulkDeleteConversations } from '@/lib/api';
import { parseUtcIso } from '@/lib/time';
import type { ConversationSummary } from '@/types';
import { BUTTON_DANGER } from '@/lib/styles';
import ConfirmModal from '@/components/layout/ConfirmModal';
import DangerConfirmModal from '@/components/layout/DangerConfirmModal';
import Checkbox from '@/components/forms/Checkbox';
import PanelSearchBar from './PanelSearchBar';
import Pagination from './Pagination';

const DEFAULT_PAGE_SIZE = 20;

export default function ConversationBrowser() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Mirror pageSize into a ref so the debounce timer body reads the
  // latest value — capturing it in the useCallback closure leaves
  // an in-flight timer using the pre-change size after the user
  // bumps "每页 X 项", overwriting the new-size fetch.
  const pageSizeRef = useRef(pageSize);

  // Selection mode state — local only, not in uiStore
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);

  const currentId = useConversationStore((s) => s.current?.id);
  const removeConversation = useConversationStore((s) => s.removeConversation);
  const setConversationBrowserVisible = useUIStore((s) => s.setConversationBrowserVisible);
  const { switchConversation } = useChat();
  const claim = useLatestOnly();

  const fetchConversations = useCallback(async (searchQuery: string, pageNum: number, size: number) => {
    // Latest-only drops slow older fetches (debounced search, stale page
    // changes) so they can't overwrite a newer result set.
    const isLatest = claim();
    setLoading(true);
    try {
      const trimmed = searchQuery.trim() || undefined;
      const offset = (pageNum - 1) * size;
      const data = await listConversations(size, offset, trimmed);
      if (!isLatest()) return;
      setConversations(data.conversations);
      setTotal(data.total);
    } catch (err) {
      if (!isLatest()) return;
      console.error('Failed to load conversations:', err);
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    fetchConversations('', 1, DEFAULT_PAGE_SIZE);
    // Mount-only initial load — handlers below own all subsequent fetches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      fetchConversations(value, 1, pageSizeRef.current);
    }, 300);
  }, [fetchConversations]);

  const handlePageChange = useCallback((p: number) => {
    setPage(p);
    fetchConversations(query, p, pageSize);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchConversations, query, pageSize]);

  const handlePageSizeChange = useCallback((size: number) => {
    setPageSize(size);
    pageSizeRef.current = size;
    setPage(1);
    fetchConversations(query, 1, size);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchConversations, query]);

  const handleSelect = useCallback(async (id: string) => {
    setConversationBrowserVisible(false);
    await switchConversation(id);
  }, [switchConversation, setConversationBrowserVisible]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteConversation(id);
      removeConversation(id);
      // Re-fetch current page so the empty slot fills from the next page;
      // step back if we just emptied the last page.
      const lastPage = Math.max(1, Math.ceil((total - 1) / pageSize));
      const nextPage = Math.min(page, lastPage);
      if (nextPage !== page) setPage(nextPage);
      fetchConversations(query, nextPage, pageSize);
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  }, [removeConversation, total, page, pageSize, query, fetchConversations]);

  const handleClose = useCallback(() => {
    setConversationBrowserVisible(false);
  }, [setConversationBrowserVisible]);

  const exitSelectionMode = useCallback(() => {
    setSelectionMode(false);
    setSelectedIds(new Set());
  }, []);

  const enterSelectionMode = useCallback(() => {
    setSelectionMode(true);
    setSelectedIds(new Set());
  }, []);

  const toggleSelection = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAllOnPage = useCallback(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const c of conversations) next.add(c.id);
      return next;
    });
  }, [conversations]);

  const handleBulkDeleteConfirm = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const res = await bulkDeleteConversations(ids);
    for (const id of res.deleted) removeConversation(id);
    setConfirmBulkDelete(false);
    exitSelectionMode();
    if (res.failed.length > 0) {
      console.warn(`Bulk delete: ${res.failed.length} failed`, res.failed);
    }
    // Re-fetch — total may have shifted enough to invalidate the current page.
    const lastPage = Math.max(1, Math.ceil((total - res.deleted.length) / pageSize));
    const nextPage = Math.min(page, lastPage);
    if (nextPage !== page) setPage(nextPage);
    fetchConversations(query, nextPage, pageSize);
  }, [selectedIds, removeConversation, exitSelectionMode, total, page, pageSize, query, fetchConversations]);

  // Esc to exit selection mode (when no modal is open — modals own their own Esc)
  useEffect(() => {
    if (!selectionMode || confirmBulkDelete) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') exitSelectionMode();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectionMode, confirmBulkDelete, exitSelectionMode]);

  const selectedCount = selectedIds.size;
  const allOnPageSelected = conversations.length > 0
    && conversations.every((c) => selectedIds.has(c.id));

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={handleQueryChange}
        placeholder="搜索对话标题..."
        disabled={selectionMode}
        countLabel={selectionMode ? null : `${total} 对话`}
        rightSlot={
          selectionMode ? (
            <span className="flex-shrink-0 text-xs text-accent">
              选择模式
            </span>
          ) : (
            <button
              onClick={enterSelectionMode}
              className="flex-shrink-0 px-2.5 py-1 text-xs rounded-md text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
              title="批量管理"
            >
              批量管理
            </button>
          )
        }
        onClose={handleClose}
      />

      {/* List */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
        {selectionMode && (
          <div className="mb-3 flex items-center gap-2 px-4 py-2.5 rounded-2xl border border-accent/40 bg-accent/5 dark:bg-accent/10">
            <span className="text-sm text-text-secondary dark:text-text-secondary-dark">
              已选 <span className="text-text-primary dark:text-text-primary-dark font-medium">{selectedCount}</span> 项
            </span>
            <button
              onClick={selectAllOnPage}
              disabled={allOnPageSelected || conversations.length === 0}
              className="px-3 py-1 text-xs rounded-md border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              全选当前页
            </button>
            <div className="flex-1" />
            <button
              onClick={exitSelectionMode}
              className="px-3 py-1 text-xs rounded-md border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              退出
            </button>
            <button
              onClick={() => setConfirmBulkDelete(true)}
              disabled={selectedCount === 0}
              className={`${BUTTON_DANGER} rounded-md px-3 py-1 text-xs`}
            >
              删除 ({selectedCount})
            </button>
          </div>
        )}
        {conversations.map((conv) => (
          <BrowserItem
            key={conv.id}
            conversation={conv}
            isActive={conv.id === currentId}
            selectionMode={selectionMode}
            selected={selectedIds.has(conv.id)}
            onSelect={handleSelect}
            onToggleSelect={toggleSelection}
            onDelete={handleDelete}
          />
        ))}

        {loading && conversations.length === 0 && (
          <div className="py-4 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            Loading...
          </div>
        )}

        {!loading && conversations.length === 0 && (
          <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
            {query ? '没有找到匹配的对话' : '暂无对话'}
          </div>
        )}
        </div>
      </div>

      {total > 0 && (
        <div className="px-4 pt-2 pb-4">
          <div className="max-w-3xl mx-auto">
            <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-2xl shadow-float px-4">
              <Pagination
                page={page}
                pageSize={pageSize}
                total={total}
                onPageChange={handlePageChange}
                onPageSizeChange={handlePageSizeChange}
                disabled={loading}
              />
            </div>
          </div>
        </div>
      )}

      {confirmBulkDelete && (
        <DangerConfirmModal
          title="批量删除对话"
          message={`将删除 ${selectedCount} 条会话，此操作不可恢复。`}
          confirmLabel="删除"
          onConfirm={handleBulkDeleteConfirm}
          onCancel={() => setConfirmBulkDelete(false)}
        />
      )}
    </div>
  );
}

function BrowserItem({
  conversation,
  isActive,
  selectionMode,
  selected,
  onSelect,
  onToggleSelect,
  onDelete,
}: {
  conversation: ConversationSummary;
  isActive: boolean;
  selectionMode: boolean;
  selected: boolean;
  onSelect: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [showMenu, setShowMenu] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { copied: copyFeedback, copy } = useCopyFeedback();
  const menuRef = useRef<HTMLDivElement>(null);
  const title = conversation.title || 'Untitled';
  const date = parseUtcIso(conversation.updated_at).toLocaleDateString();

  const handleCopyId = async () => {
    await copy(conversation.id);
    setMenuOpen(false);
  };

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [menuOpen]);

  const handleRowClick = () => {
    if (selectionMode) {
      onToggleSelect(conversation.id);
    } else {
      onSelect(conversation.id);
    }
  };

  return (
    <>
      <div
        className={`group relative cursor-pointer transition-colors rounded-lg mb-1 ${
          menuOpen ? 'z-40' : ''
        } ${
          selectionMode && selected
            ? 'bg-accent/10 dark:bg-accent/15 px-4 py-3'
            : isActive
            ? 'bg-panel dark:bg-panel-accent-dark px-4 py-3'
            : 'hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60 px-4 py-3'
        }`}
        onClick={handleRowClick}
        onMouseEnter={() => setShowMenu(true)}
        onMouseLeave={() => { if (!menuOpen) setShowMenu(false); }}
      >
        <div className="flex items-center gap-3">
          {selectionMode && (
            <Checkbox
              checked={selected}
              onChange={() => onToggleSelect(conversation.id)}
              onClick={(e) => e.stopPropagation()}
              ariaLabel={`选中 ${title}`}
            />
          )}
          <div className="flex-1 min-w-0">
            <div className={`font-medium text-text-primary dark:text-text-primary-dark truncate ${(showMenu || menuOpen) && !selectionMode ? 'pr-8' : ''}`}>
              {title}
            </div>
            <div className="flex items-center gap-2 mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              <span>{date}</span>
              <span>{conversation.message_count} messages</span>
              {copyFeedback && <span className="text-accent">ID copied</span>}
            </div>
          </div>
        </div>

        {/* ··· menu trigger — hidden in selection mode */}
        {!selectionMode && (showMenu || menuOpen) && (
          <div ref={menuRef} className="absolute right-3 top-1/2 -translate-y-1/2">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpen((prev) => !prev);
              }}
              className="p-1.5 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-surface-dark transition-colors"
              aria-label="More actions"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <circle cx="8" cy="3" r="1.5" />
                <circle cx="8" cy="8" r="1.5" />
                <circle cx="8" cy="13" r="1.5" />
              </svg>
            </button>

            {menuOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 w-40 bg-surface dark:bg-panel-dark border border-border dark:border-border-dark rounded-lg shadow-modal p-1">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCopyId();
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-surface-dark rounded-md transition-colors"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <rect x="5" y="5" width="9" height="9" rx="1.5" />
                    <path d="M5 11H3.5A1.5 1.5 0 0 1 2 9.5v-7A1.5 1.5 0 0 1 3.5 1h7A1.5 1.5 0 0 1 12 2.5V5" />
                  </svg>
                  复制 ID
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuOpen(false);
                    setConfirmDelete(true);
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm text-status-error hover:bg-status-error/10 rounded-md transition-colors"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
                  </svg>
                  删除对话
                </button>
              </div>
            )}
          </div>
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

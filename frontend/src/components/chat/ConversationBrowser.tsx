'use client';

import { useState, useCallback, useEffect, useRef, } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useUIStore } from '@/stores/uiStore';
import { useChat } from '@/hooks/useChat';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { listConversations, deleteConversation, bulkDeleteConversations } from '@/lib/api';
import { parseUtcIso } from '@/lib/time';
import { formatBytes } from '@/lib/formatBytes';
import type { ConversationSummary } from '@/types';
import { BUTTON_DANGER, MENU_ROW_HOVER } from '@/lib/styles';
import DangerConfirmModal from '@/components/layout/DangerConfirmModal';
import ConversationActionsMenu from '@/components/sidebar/ConversationActionsMenu';
import Checkbox from '@/components/forms/Checkbox';
import { StatusNotice } from '@/components/ui/StatusNotice';
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
  const [bulkDeleteNotice, setBulkDeleteNotice] = useState<string | null>(null);

  const currentId = useConversationStore((s) => s.current?.id);
  const removeConversation = useConversationStore((s) => s.removeConversation);
  const setActiveMode = useUIStore((s) => s.setActiveMode);
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
    setActiveMode('none');
    await switchConversation(id);
  }, [switchConversation, setActiveMode]);

  const handleDelete = useCallback(async (id: string) => {
    // 让失败冒泡到 ConversationActionsMenu 的 DangerConfirmModal；它负责将
    // stale active hint 导致的 409 显示给用户并保持弹窗，而不是静默关闭。
    await deleteConversation(id);
    removeConversation(id);
    // Re-fetch current page so the empty slot fills from the next page;
    // step back if we just emptied the last page.
    const lastPage = Math.max(1, Math.ceil((total - 1) / pageSize));
    const nextPage = Math.min(page, lastPage);
    if (nextPage !== page) setPage(nextPage);
    fetchConversations(query, nextPage, pageSize);
  }, [removeConversation, total, page, pageSize, query, fetchConversations]);

  const handleClose = useCallback(() => {
    setActiveMode('none');
  }, [setActiveMode]);

  const exitSelectionMode = useCallback(() => {
    setSelectionMode(false);
    setSelectedIds(new Set());
    setBulkDeleteNotice(null);
  }, []);

  const enterSelectionMode = useCallback(() => {
    setSelectionMode(true);
    setSelectedIds(new Set());
    setBulkDeleteNotice(null);
  }, []);

  const toggleSelection = useCallback((id: string) => {
    if (conversations.some((c) => c.id === id && c.active_message_id)) return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, [conversations]);

  const selectAllOnPage = useCallback(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const c of conversations) {
        if (!c.active_message_id) next.add(c.id);
      }
      return next;
    });
  }, [conversations]);

  const handleBulkDeleteConfirm = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const res = await bulkDeleteConversations(ids);
    for (const id of res.deleted) removeConversation(id);
    setConfirmBulkDelete(false);

    const activeFailures = res.failed.filter(
      (item) => item.reason === 'active_execution',
    );
    if (res.failed.length > 0) {
      // active_message_id 只是选择时的 best-effort 提示；服务端 lease 才是
      // authority。竞态失败的活跃项保留选择，其他 not_found 项刷新后会消失。
      setSelectedIds(new Set(activeFailures.map((item) => item.id)));
      setSelectionMode(activeFailures.length > 0);

      const messages = [`已删除 ${res.deleted.length} 条。`];
      if (activeFailures.length > 0) {
        messages.push(
          `${activeFailures.length} 条任务正在运行，已保留选择，完成或取消后可重试。`,
        );
      }
      const unavailableCount = res.failed.length - activeFailures.length;
      if (unavailableCount > 0) {
        messages.push(`${unavailableCount} 条已不存在或无权访问。`);
      }
      setBulkDeleteNotice(messages.join(''));
    } else {
      setSelectionMode(false);
      setSelectedIds(new Set());
      setBulkDeleteNotice(null);
    }
    // Re-fetch — total may have shifted enough to invalidate the current page.
    const lastPage = Math.max(1, Math.ceil((total - res.deleted.length) / pageSize));
    const nextPage = Math.min(page, lastPage);
    if (nextPage !== page) setPage(nextPage);
    fetchConversations(query, nextPage, pageSize);
  }, [selectedIds, removeConversation, total, page, pageSize, query, fetchConversations]);

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
  const deletableOnPage = conversations.filter((c) => !c.active_message_id);
  const allOnPageSelected = deletableOnPage.length > 0
    && deletableOnPage.every((c) => selectedIds.has(c.id));

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={handleQueryChange}
        placeholder="搜索对话标题…"
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
              className="flex-shrink-0 h-11 sm:h-auto px-3 sm:px-2.5 py-1 text-xs rounded-md text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
              title="批量管理"
            >
              批量管理
            </button>
          )
        }
        onClose={handleClose}
      />

      {bulkDeleteNotice && (
        <div className="px-4 pb-3">
          <div className="max-w-3xl mx-auto">
            <StatusNotice
              tone="warning"
              onDismiss={() => setBulkDeleteNotice(null)}
            >
              {bulkDeleteNotice}
            </StatusNotice>
          </div>
        </div>
      )}

      {/* List */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
        {selectionMode && (
          <div className="mb-3 flex flex-wrap items-center gap-2 px-3 sm:px-4 py-2.5 rounded-xl border border-accent/40 bg-accent/5 dark:bg-accent/10">
            <span className="text-sm text-text-secondary dark:text-text-secondary-dark">
              已选 <span className="text-text-primary dark:text-text-primary-dark font-medium">{selectedCount}</span> 项
            </span>
            <button
              onClick={selectAllOnPage}
              disabled={allOnPageSelected || deletableOnPage.length === 0}
              className="min-h-11 sm:min-h-0 px-3 py-1 text-xs rounded-md border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              全选当前页
            </button>
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={exitSelectionMode}
                className="min-h-11 sm:min-h-0 px-3 py-1 text-xs rounded-md border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
              >
                退出
              </button>
              <button
                onClick={() => setConfirmBulkDelete(true)}
                disabled={selectedCount === 0}
                className={`${BUTTON_DANGER} min-h-11 sm:min-h-0 rounded-lg px-3 py-1 text-xs`}
              >
                删除 ({selectedCount})
              </button>
            </div>
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
      )}

      {confirmBulkDelete && (
        <DangerConfirmModal
          title="批量删除对话"
          message={`将删除 ${selectedCount} 条会话。\n操作不可恢复。`}
          confirmLabel="确认删除"
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
  const title = conversation.title || 'Untitled';
  const date = parseUtcIso(conversation.updated_at).toLocaleDateString();

  const handleRowClick = () => {
    if (selectionMode) {
      onToggleSelect(conversation.id);
    } else {
      onSelect(conversation.id);
    }
  };

  return (
    <div
      className={`group relative cursor-pointer transition-colors rounded-lg mb-1 ${
          menuOpen ? 'z-40' : ''
        } ${
          selectionMode && selected
            ? 'bg-accent/10 dark:bg-accent/15 px-4 py-3'
            : isActive
            ? 'bg-panel dark:bg-panel-accent-dark px-4 py-3'
            : `${MENU_ROW_HOVER} px-4 py-3`
        }`}
        onClick={handleRowClick}
        onMouseEnter={() => setShowMenu(true)}
        onMouseLeave={() => setShowMenu(false)}
      >
        <div className="flex items-center gap-3">
          {selectionMode && (
            <span title={conversation.active_message_id ? '任务运行中，暂不能删除' : undefined}>
              <Checkbox
                checked={selected}
                disabled={Boolean(conversation.active_message_id)}
                onChange={() => onToggleSelect(conversation.id)}
                onClick={(e) => e.stopPropagation()}
                ariaLabel={`选中 ${title}`}
              />
            </span>
          )}
          <div className="flex-1 min-w-0">
            <div className={`font-medium text-text-primary dark:text-text-primary-dark truncate ${(showMenu || menuOpen) && !selectionMode ? 'pr-8' : ''}`}>
              {title}
            </div>
            <div className="flex items-center gap-2 mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              <span>{date}</span>
              <span>{conversation.message_count} messages</span>
              {conversation.upload_bytes > 0 && (
                <span title="附件占用">{formatBytes(conversation.upload_bytes)}</span>
              )}
            </div>
          </div>
        </div>

        {/* ··· 操作菜单(复制 ID / 删除对话)—— 与侧栏 ConversationItem 共用实现。
            选择模式下整条 hover 触发器都隐藏,故 visible 额外带上 !selectionMode。 */}
        <ConversationActionsMenu
          conversationId={conversation.id}
          title={title}
          visible={!selectionMode && (showMenu || menuOpen)}
          open={menuOpen}
          onOpenChange={setMenuOpen}
          onDelete={onDelete}
          deleteDisabled={Boolean(conversation.active_message_id)}
          wrapperClassName="absolute right-3 top-1/2 -translate-y-1/2"
          triggerClassName="p-1.5 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-surface-dark transition-colors"
        />
      </div>
  );
}

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { FeedbackRatingIcon } from '@/components/ui/FeedbackRatingIcon';
import PanelSearchBar from '@/components/ui/PanelSearchBar';
import Pagination from '@/components/ui/Pagination';
import { PillBadge } from '@/components/ui/PillBadge';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import * as api from '@/lib/api';
import type { AdminConversationSummary, AdminFeedbackItem } from '@/lib/api';
import { FEEDBACK_TAG_LABELS } from '@/lib/messageFeedback';
import { MENU_ROW_HOVER } from '@/lib/styles';
import { parseUtcIso } from '@/lib/time';
import { useUIStore } from '@/stores/uiStore';
import { formatAdminInputPreview } from './adminLiveEvents';

const DEFAULT_PAGE_SIZE = 20;

function formatDateTime(iso: string): string {
  try {
    return parseUtcIso(iso).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

export function AdminConversationBrowser({
  onSelect,
  onClose,
}: {
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const [conversations, setConversations] = useState<AdminConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const queryRef = useRef(query);
  const pageRef = useRef(page);
  const pageSizeRef = useRef(pageSize);
  const refreshTick = useUIStore((s) => s.observabilityRefreshTick);
  const claim = useLatestOnly();

  const fetchConversations = useCallback(async (
    q: string,
    pageNum: number,
    size: number,
  ) => {
    const isLatest = claim();
    setLoading(true);
    try {
      const trimmed = q.trim() || undefined;
      const offset = (pageNum - 1) * size;
      const res = await api.listAdminConversations(size, offset, trimmed);
      if (!isLatest()) return;
      const lastPage = Math.max(1, Math.ceil(res.total / size));
      if (pageNum > lastPage) {
        pageRef.current = lastPage;
        setPage(lastPage);
        void fetchConversations(q, lastPage, size);
        return;
      }
      setConversations(res.conversations);
      setTotal(res.total);
    } catch (err) {
      if (!isLatest()) return;
      console.error('Failed to load admin conversations:', err);
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    fetchConversations(queryRef.current, pageRef.current, pageSizeRef.current);
  }, [fetchConversations, refreshTick]);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    queryRef.current = value;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      pageRef.current = 1;
      fetchConversations(value, 1, pageSizeRef.current);
    }, 300);
  }, [fetchConversations]);

  const handlePageChange = useCallback((value: number) => {
    setPage(value);
    pageRef.current = value;
    fetchConversations(queryRef.current, value, pageSizeRef.current);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchConversations]);

  const handlePageSizeChange = useCallback((value: number) => {
    setPageSize(value);
    pageSizeRef.current = value;
    setPage(1);
    pageRef.current = 1;
    fetchConversations(queryRef.current, 1, value);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchConversations]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={handleQueryChange}
        placeholder="搜索对话标题或 ID…"
        countLabel={`${total} 对话`}
        onClose={onClose}
      />

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {conversations.map((conversation) => (
            <div
              key={conversation.id}
              className={`group relative cursor-pointer transition-colors rounded-lg mb-1 px-4 py-3 ${MENU_ROW_HOVER}`}
              onClick={() => onSelect(conversation.id)}
            >
              <div className="flex items-center gap-2">
                {conversation.is_active && (
                  <span
                    className="inline-block w-2 h-2 rounded-full bg-status-running flex-shrink-0"
                    title="运行中"
                  />
                )}
                <span className="font-medium text-text-primary dark:text-text-primary-dark truncate">
                  {conversation.title || 'Untitled'}
                </span>
              </div>
              <div className="flex items-center gap-2 mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                <span>{conversation.user_display_name || conversation.user_id || '-'}</span>
                <span>{conversation.message_count} messages</span>
                <span>{parseUtcIso(conversation.updated_at).toLocaleDateString()}</span>
              </div>
            </div>
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
    </div>
  );
}

type FeedbackFilter = 'all' | 'positive' | 'negative';

export function AdminFeedbackBrowser({
  onSelect,
  onClose,
}: {
  onSelect: (conversationId: string, messageId: string) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<AdminFeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FeedbackFilter>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const queryRef = useRef(query);
  const filterRef = useRef(filter);
  const pageRef = useRef(page);
  const pageSizeRef = useRef(pageSize);
  const refreshTick = useUIStore((s) => s.observabilityRefreshTick);
  const claim = useLatestOnly();

  const fetchFeedback = useCallback(async (
    q: string,
    selectedFilter: FeedbackFilter,
    pageNum: number,
    size: number,
  ) => {
    const isLatest = claim();
    setLoading(true);
    try {
      const res = await api.listAdminFeedback(
        size,
        (pageNum - 1) * size,
        q.trim() || undefined,
        selectedFilter === 'all' ? undefined : selectedFilter,
      );
      if (!isLatest()) return;
      const lastPage = Math.max(1, Math.ceil(res.total / size));
      if (pageNum > lastPage) {
        pageRef.current = lastPage;
        setPage(lastPage);
        void fetchFeedback(q, selectedFilter, lastPage, size);
        return;
      }
      setItems(res.feedback);
      setTotal(res.total);
    } catch (error) {
      if (isLatest()) console.error('Failed to load admin feedback:', error);
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    fetchFeedback(
      queryRef.current,
      filterRef.current,
      pageRef.current,
      pageSizeRef.current,
    );
  }, [fetchFeedback, refreshTick]);

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    queryRef.current = value;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      pageRef.current = 1;
      setPage(1);
      fetchFeedback(value, filterRef.current, 1, pageSizeRef.current);
    }, 300);
  }, [fetchFeedback]);

  const handleFilterChange = useCallback((value: FeedbackFilter) => {
    setFilter(value);
    filterRef.current = value;
    pageRef.current = 1;
    setPage(1);
    fetchFeedback(queryRef.current, value, 1, pageSizeRef.current);
  }, [fetchFeedback]);

  const handlePageChange = useCallback((value: number) => {
    setPage(value);
    pageRef.current = value;
    fetchFeedback(queryRef.current, filterRef.current, value, pageSizeRef.current);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchFeedback]);

  const handlePageSizeChange = useCallback((value: number) => {
    setPageSize(value);
    pageSizeRef.current = value;
    pageRef.current = 1;
    setPage(1);
    fetchFeedback(queryRef.current, filterRef.current, 1, value);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [fetchFeedback]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={handleQueryChange}
        placeholder="搜索对话标题、对话 ID 或消息 ID…"
        countLabel={`${total} 条反馈`}
        onClose={onClose}
      />
      <div className="px-4 pb-3">
        <div className="max-w-3xl mx-auto">
          <SegmentedTabs
            value={filter}
            ariaLabel="反馈类型筛选"
            options={[
              { value: 'all', label: '全部' },
              {
                value: 'positive',
                label: (
                  <span className="inline-flex items-center gap-1 text-status-success">
                    <FeedbackRatingIcon rating="positive" size={13} />赞
                  </span>
                ),
              },
              {
                value: 'negative',
                label: (
                  <span className="inline-flex items-center gap-1 text-status-error">
                    <FeedbackRatingIcon rating="negative" size={13} />踩
                  </span>
                ),
              },
            ]}
            onChange={handleFilterChange}
          />
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {items.map((item) => (
            <button
              key={item.message_id}
              type="button"
              onClick={() => onSelect(item.conversation_id, item.message_id)}
              className={`w-full text-left rounded-lg mb-1 px-4 py-3 transition-colors ${MENU_ROW_HOVER}`}
            >
              <div className="flex items-center gap-2">
                <PillBadge
                  tone={item.feedback.rating === 'positive' ? 'success' : 'error'}
                  size="regular"
                  className="gap-1"
                >
                  <FeedbackRatingIcon rating={item.feedback.rating} size={14} />
                  {item.feedback.rating === 'positive' ? '赞' : '踩'}
                </PillBadge>
                <span className="font-medium text-text-primary dark:text-text-primary-dark truncate">
                  {item.conversation_title || 'Untitled'}
                </span>
                <span className="ml-auto shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  {formatDateTime(item.feedback.updated_at)}
                </span>
              </div>
              <div className="mt-1 truncate text-sm text-text-secondary dark:text-text-secondary-dark">
                {formatAdminInputPreview(item.user_input)}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                <span>{item.user_display_name || item.user_id || '-'}</span>
                <span className="font-mono" title={item.message_id}>{item.message_id}</span>
                {item.feedback.tags.map((tag) => (
                  <PillBadge key={tag} tone="neutral">{FEEDBACK_TAG_LABELS[tag]}</PillBadge>
                ))}
              </div>
              {item.feedback.detail ? (
                <div className="mt-1 truncate text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  {item.feedback.detail}
                </div>
              ) : null}
            </button>
          ))}

          {loading && items.length === 0 ? (
            <div className="py-4 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
              Loading...
            </div>
          ) : null}
          {!loading && items.length === 0 ? (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              {query ? '没有找到匹配的反馈' : '暂无反馈'}
            </div>
          ) : null}
        </div>
      </div>

      {total > 0 ? (
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
      ) : null}
    </div>
  );
}

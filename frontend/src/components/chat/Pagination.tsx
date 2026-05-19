'use client';

import { useMemo } from 'react';

interface PaginationProps {
  /** 1-based current page. */
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  /** Page-size options, default [20, 50, 100]. */
  pageSizeOptions?: number[];
  /** Disable controls (e.g. while loading). */
  disabled?: boolean;
}

const DEFAULT_PAGE_SIZE_OPTIONS = [20, 50, 100];

/**
 * Up to 7 slots: first / ellipsis / window of 3-5 / ellipsis / last.
 * Avoids a window that jumps around as the user steps through pages.
 */
function getPageItems(page: number, totalPages: number): (number | 'ellipsis')[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  if (page <= 4) {
    return [1, 2, 3, 4, 5, 'ellipsis', totalPages];
  }
  if (page >= totalPages - 3) {
    return [1, 'ellipsis', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
  }
  return [1, 'ellipsis', page - 1, page, page + 1, 'ellipsis', totalPages];
}

export default function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  disabled = false,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const items = useMemo(() => getPageItems(page, totalPages), [page, totalPages]);

  if (total === 0) return null;

  const canPrev = page > 1 && !disabled;
  const canNext = page < totalPages && !disabled;

  return (
    <div className="flex items-center justify-between gap-3 py-3 text-sm">
      {totalPages > 1 ? (
        <div className="flex items-center gap-1">
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={!canPrev}
            className="px-2 py-1 rounded-md text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent transition-colors"
            aria-label="上一页"
          >
            ‹
          </button>
          {items.map((item, idx) =>
            item === 'ellipsis' ? (
              <span
                key={`e${idx}`}
                className="px-1 text-text-tertiary dark:text-text-tertiary-dark select-none"
              >
                …
              </span>
            ) : (
              <button
                key={item}
                onClick={() => onPageChange(item)}
                disabled={disabled}
                aria-current={item === page ? 'page' : undefined}
                className={`min-w-[28px] px-2 py-1 rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                  item === page
                    ? 'bg-accent text-white'
                    : 'text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark'
                }`}
              >
                {item}
              </button>
            ),
          )}
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={!canNext}
            className="px-2 py-1 rounded-md text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent transition-colors"
            aria-label="下一页"
          >
            ›
          </button>
        </div>
      ) : (
        // Keep the flex-row balanced so the page-size selector stays right-aligned.
        <div />
      )}

      <label className="flex items-center gap-2 text-text-secondary dark:text-text-secondary-dark">
        每页
        <div className="relative">
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            disabled={disabled}
            className="appearance-none pr-9 px-3 py-1.5 rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark focus:outline-none focus:border-accent dark:focus:border-accent disabled:opacity-40"
          >
            {pageSizeOptions.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
          <svg
            className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary dark:text-text-tertiary-dark"
            width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"
          >
            <path d="M3 4.5l3 3 3-3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        项
      </label>
    </div>
  );
}

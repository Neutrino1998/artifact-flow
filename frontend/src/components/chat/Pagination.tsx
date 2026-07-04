'use client';

import { useMemo } from 'react';
import { BUTTON_GHOST_ICON, SELECT_COMPACT } from '@/lib/styles';
import { SELECT_CHEVRON_COMPACT } from '@/components/ui/SelectChevron';

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
    <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-2xl px-4 flex items-center justify-between gap-3 py-2 text-sm">
      {totalPages > 1 ? (
        <div className="flex items-center gap-1">
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={!canPrev}
            className={`${BUTTON_GHOST_ICON} px-2 py-1`}
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
                className={
                  item === page
                    ? 'min-w-[28px] px-2 py-1 rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-accent/10 dark:bg-accent/15 text-accent font-medium'
                    : `${BUTTON_GHOST_ICON} min-w-[28px] px-2 py-1`
                }
              >
                {item}
              </button>
            ),
          )}
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={!canNext}
            className={`${BUTTON_GHOST_ICON} px-2 py-1`}
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
            className={SELECT_COMPACT}
          >
            {pageSizeOptions.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
          {SELECT_CHEVRON_COMPACT}
        </div>
        项
      </label>
    </div>
  );
}

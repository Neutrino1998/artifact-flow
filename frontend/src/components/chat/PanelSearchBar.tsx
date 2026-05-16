'use client';

import type { ReactNode } from 'react';

interface PanelSearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Right-aligned label, e.g. "{total} 用户". Omit to hide. */
  countLabel?: ReactNode;
  /** Close button handler. Omit to hide the close button. */
  onClose?: () => void;
  /**
   * Extra content rendered between the count label and the close button.
   * Use for mode toggles (e.g. ConversationBrowser "批量管理" button /
   * "选择模式" indicator).
   */
  rightSlot?: ReactNode;
  /** Disable the input (e.g. in bulk-selection mode). */
  disabled?: boolean;
  /** Focus on mount. Default true to match existing call sites. */
  autoFocus?: boolean;
}

/**
 * Sticky search header used at the top of full-width middle-panel browsers
 * (UserManagement, ConversationBrowser, Observability). Renders the
 * outer `px-4 pt-4 pb-2` + `max-w-3xl mx-auto` shell so callers don't repeat
 * the layout chrome.
 */
export default function PanelSearchBar({
  value,
  onChange,
  placeholder,
  countLabel,
  onClose,
  rightSlot,
  disabled = false,
  autoFocus = true,
}: PanelSearchBarProps) {
  return (
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
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            autoFocus={autoFocus}
            disabled={disabled}
            className="flex-1 bg-transparent text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none disabled:opacity-50"
          />
          {countLabel !== undefined && countLabel !== null && (
            <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              {countLabel}
            </span>
          )}
          {rightSlot}
          {onClose && (
            <button
              onClick={onClose}
              className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
              aria-label="关闭"
              title="关闭"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

'use client';

import { memo, useState, type ReactNode } from 'react';

interface FlowBlockProps {
  /** Badge element shown in the header (colored pill with icon + label) */
  badge: ReactNode;
  /** Optional right-aligned metadata (tokens, timing, etc.) */
  extra?: ReactNode;
  /** Collapsible body. If omitted, the block is not expandable. */
  body?: ReactNode;
  defaultExpanded?: boolean;
  /** When false, clicking the header does nothing (used for non-expandable states like compaction-running) */
  canToggle?: boolean;
}

/**
 * Shared base for non-agent inline blocks (inject, compaction).
 *
 * Visual language: accent-colored border distinguishes these from regular
 * agent segments (which use the neutral `border` token). The collapsible
 * header layout mirrors AgentSegmentBlock for consistency.
 */
function FlowBlock({
  badge,
  extra,
  body,
  defaultExpanded = false,
  canToggle = true,
}: FlowBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const hasBody = !!body;
  const isExpandable = hasBody && canToggle;
  const isExpanded = isExpandable && expanded;

  return (
    <div className="bg-chat dark:bg-chat-dark border border-accent/40 rounded-card overflow-hidden">
      <button
        type="button"
        onClick={() => { if (isExpandable) setExpanded(!expanded); }}
        className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors ${
          isExpandable
            ? 'hover:bg-bg dark:hover:bg-bg-dark cursor-pointer'
            : 'cursor-default'
        }`}
      >
        {/* Chevron — only when expandable */}
        {isExpandable && (
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            className={`flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark transition-transform ${
              isExpanded ? 'rotate-90' : ''
            }`}
          >
            <path d="M4.5 2.5 8 6l-3.5 3.5" />
          </svg>
        )}

        {badge}

        {extra && (
          <span className="ml-auto text-xs text-text-tertiary dark:text-text-tertiary-dark font-mono">
            {extra}
          </span>
        )}
      </button>

      {isExpanded && body && <div className="px-3 pb-3">{body}</div>}
    </div>
  );
}

export default memo(FlowBlock);

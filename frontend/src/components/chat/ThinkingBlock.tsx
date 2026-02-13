'use client';

import { memo, useState, useEffect } from 'react';
import CyclingDots from './CyclingDots';

interface ThinkingBlockProps {
  content: string;
  defaultExpanded?: boolean;
  isLive?: boolean;
}

function ThinkingBlock({ content, defaultExpanded = false, isLive = false }: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  // Auto-expand when live thinking starts, auto-collapse when it ends.
  // Only react to isLive transitions — not content changes — so user can
  // freely toggle during streaming without the effect overriding them.
  useEffect(() => {
    setExpanded(isLive);
  }, [isLive]);

  if (!content) return null;

  return (
    <div className="border border-border dark:border-border-dark rounded-card overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className={`transition-transform ${expanded ? 'rotate-90' : ''}`}
        >
          <path d="M4.5 2.5 8 6l-3.5 3.5" />
        </svg>
        <span>
          Thinking{isLive && <CyclingDots />}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-3 pt-2 text-xs text-text-tertiary dark:text-text-tertiary-dark whitespace-pre-wrap font-mono leading-relaxed max-h-60 overflow-y-auto border-t border-border dark:border-border-dark">
          {content}
        </div>
      )}
    </div>
  );
}

export default memo(ThinkingBlock);

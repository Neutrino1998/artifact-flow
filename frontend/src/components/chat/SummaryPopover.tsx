'use client';

import { memo, useState, useRef, useEffect, useCallback } from 'react';

interface SummaryPopoverProps {
  summary: string;
}

function SummaryPopover({ summary }: SummaryPopoverProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleClickOutside = useCallback((e: MouseEvent) => {
    if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
      setOpen(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      document.addEventListener('click', handleClickOutside, true);
      return () => document.removeEventListener('click', handleClickOutside, true);
    }
  }, [open, handleClickOutside]);

  return (
    <div ref={containerRef} className="relative flex items-center">
      <button
        onClick={() => setOpen(!open)}
        className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
        aria-label="View summary"
        title="压缩摘要"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
          <path d="M3.27 6.96 12 12.01l8.73-5.05M12 22.08V12" />
        </svg>
      </button>
      {open && (
        <div className="absolute bottom-full left-0 mb-2 w-72 bg-panel dark:bg-surface-dark rounded-card shadow-lg border border-border dark:border-border-dark p-3 z-50">
          <div className="text-xs font-medium text-text-secondary dark:text-text-secondary-dark mb-1.5">
            压缩摘要
          </div>
          <div className="text-xs text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words">
            {summary}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(SummaryPopover);

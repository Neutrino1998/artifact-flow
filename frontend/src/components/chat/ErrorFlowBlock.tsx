'use client';

import { memo } from 'react';

interface ErrorFlowBlockProps {
  message?: string;
}

function ErrorFlowBlock({ message }: ErrorFlowBlockProps) {
  return (
    <div className="bg-chat dark:bg-chat-dark border border-red-500/40 rounded-card overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 text-xs">
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium bg-red-500/10 text-red-600 dark:text-red-400">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
          </svg>
          error
        </span>
      </div>
      {message && (
        <div className="px-3 pb-3 text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap break-words">
          {message}
        </div>
      )}
    </div>
  );
}

export default memo(ErrorFlowBlock);

'use client';

import { memo } from 'react';

interface InjectFlowBlockProps {
  content?: string;
}

function InjectFlowBlock({ content }: InjectFlowBlockProps) {
  return (
    <div className="bg-chat dark:bg-chat-dark border border-accent/40 rounded-card overflow-hidden">
      {/* Header — same layout as AgentSegmentBlock but non-collapsible */}
      <div className="flex items-center gap-2 px-3 py-2 text-xs">
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium bg-accent/10 text-accent">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
          inject
        </span>
      </div>
      {/* Body */}
      {content && (
        <div className="px-3 pb-3 text-xs text-text-secondary dark:text-text-secondary-dark whitespace-pre-wrap break-words">
          {content}
        </div>
      )}
    </div>
  );
}

export default memo(InjectFlowBlock);

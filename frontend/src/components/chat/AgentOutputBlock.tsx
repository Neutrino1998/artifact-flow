'use client';

import { memo, useState } from 'react';

interface AgentOutputBlockProps {
  content: string;
  defaultExpanded?: boolean;
}

function AgentOutputBlock({ content, defaultExpanded = false }: AgentOutputBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

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
        Agent Output
      </button>
      {expanded && (
        <div className="px-3 pb-3 text-xs text-text-tertiary dark:text-text-tertiary-dark whitespace-pre-wrap font-mono leading-relaxed max-h-60 overflow-y-auto">
          {content}
        </div>
      )}
    </div>
  );
}

export default memo(AgentOutputBlock);

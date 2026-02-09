'use client';

import { memo } from 'react';

interface AgentBadgeProps {
  agent: string;
  status: 'running' | 'complete';
}

function AgentBadge({ agent, status }: AgentBadgeProps) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${
          status === 'running'
            ? 'bg-accent/10 text-accent'
            : 'bg-bg dark:bg-bg-dark text-text-secondary dark:text-text-secondary-dark'
        }`}
      >
        {status === 'running' && (
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
        )}
        {agent}
      </span>
    </div>
  );
}

export default memo(AgentBadge);

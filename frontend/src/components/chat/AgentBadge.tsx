'use client';

import { memo } from 'react';
import { PillBadge } from '@/components/ui/PillBadge';

interface AgentBadgeProps {
  agent: string;
  status: 'running' | 'complete';
}

function AgentBadge({ agent, status }: AgentBadgeProps) {
  return (
    <div className="flex items-center gap-2">
      <PillBadge
        tone={status === 'running' ? 'accent' : 'neutral'}
        size="regular"
        className="gap-1.5"
      >
        {status === 'running' && (
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
        )}
        {agent}
      </PillBadge>
    </div>
  );
}

export default memo(AgentBadge);

'use client';

import { memo } from 'react';
import CyclingDots from './CyclingDots';
import DisclosureRow from './DisclosureRow';

interface ThinkingBlockProps {
  content: string;
  defaultExpanded?: boolean;
  isLive?: boolean;
}

function ThinkingBlock({ content, defaultExpanded = false, isLive = false }: ThinkingBlockProps) {
  if (!content) return null;

  return (
    <DisclosureRow
      variant="inline"
      label={<span>Thinking{isLive && <CyclingDots />}</span>}
      defaultExpanded={defaultExpanded}
      isLive={isLive}
      headerClassName="text-text-secondary dark:text-text-secondary-dark"
      bodyClassName="pl-5 pt-1 pb-2"
    >
      <div className="bg-panel-accent dark:bg-bg-dark rounded p-2 text-xs text-text-tertiary dark:text-text-tertiary-dark whitespace-pre-wrap font-mono leading-relaxed max-h-60 overflow-y-auto">
        {content}
      </div>
    </DisclosureRow>
  );
}

export default memo(ThinkingBlock);

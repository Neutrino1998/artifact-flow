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
      label={<span>Thinking{isLive && <CyclingDots />}</span>}
      defaultExpanded={defaultExpanded}
      isLive={isLive}
      headerClassName="text-text-secondary dark:text-text-secondary-dark"
      bodyClassName="px-3 pb-3 pt-2 text-xs text-text-tertiary dark:text-text-tertiary-dark whitespace-pre-wrap font-mono leading-relaxed max-h-60 overflow-y-auto"
    >
      {content}
    </DisclosureRow>
  );
}

export default memo(ThinkingBlock);

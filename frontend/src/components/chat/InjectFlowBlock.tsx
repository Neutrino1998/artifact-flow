'use client';

import { memo } from 'react';
import FlowBlock from './FlowBlock';
import MarkdownBlock from '@/components/markdown/MarkdownBlock';
import { PillBadge } from '@/components/ui/PillBadge';
import CyclingDots from './CyclingDots';

interface InjectFlowBlockProps {
  content?: string;
  pending?: boolean;
}

const InjectBadge = ({ pending }: { pending?: boolean }) => (
  <PillBadge tone="accent" size="regular" className="gap-1.5">
    {pending ? (
      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
    ) : (
      <svg
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 5v14M5 12h14" />
      </svg>
    )}
    {pending ? 'inject pending' : 'inject'}
  </PillBadge>
);

function InjectFlowBlock({ content, pending }: InjectFlowBlockProps) {
  const body = content ? (
    <div className={pending ? 'opacity-80' : undefined}>
      <MarkdownBlock>{content}</MarkdownBlock>
    </div>
  ) : undefined;

  return (
    <FlowBlock
      badge={<InjectBadge pending={pending} />}
      extra={pending ? <span>waiting<CyclingDots /></span> : undefined}
      body={body}
      defaultExpanded
    />
  );
}

export default memo(InjectFlowBlock);

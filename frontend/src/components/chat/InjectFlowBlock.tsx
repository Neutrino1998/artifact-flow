'use client';

import { memo } from 'react';
import FlowBlock from './FlowBlock';
import MarkdownBlock from '@/components/markdown/MarkdownBlock';
import { PillBadge } from '@/components/ui/PillBadge';

interface InjectFlowBlockProps {
  content?: string;
}

const InjectBadge = () => (
  <PillBadge tone="accent" size="regular" className="gap-1.5">
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
    inject
  </PillBadge>
);

function InjectFlowBlock({ content }: InjectFlowBlockProps) {
  const body = content ? (
    <MarkdownBlock>{content}</MarkdownBlock>
  ) : undefined;

  return <FlowBlock badge={<InjectBadge />} body={body} defaultExpanded />;
}

export default memo(InjectFlowBlock);

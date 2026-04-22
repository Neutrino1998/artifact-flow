'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import FlowBlock from './FlowBlock';
import { PROSE_CLASSES } from '@/lib/styles';
import { markdownComponents } from '@/components/markdown';

interface InjectFlowBlockProps {
  content?: string;
}

const InjectBadge = () => (
  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium bg-accent/10 text-accent">
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
  </span>
);

function InjectFlowBlock({ content }: InjectFlowBlockProps) {
  const body = content ? (
    <div className={PROSE_CLASSES}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  ) : undefined;

  return <FlowBlock badge={<InjectBadge />} body={body} defaultExpanded />;
}

export default memo(InjectFlowBlock);

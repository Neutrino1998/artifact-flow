'use client';

import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { useStreamStore } from '@/stores/streamStore';
import AgentSegmentBlock from './AgentSegmentBlock';

interface AssistantMessageProps {
  content: string;
  messageId?: string;
}

function AssistantMessage({ content, messageId }: AssistantMessageProps) {
  const [copied, setCopied] = useState(false);
  const completedSegs = useStreamStore(
    (s) => messageId ? s.completedSegments.get(messageId) : undefined
  );

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="group relative">
      {/* Completed execution segments (session-only, collapsible) */}
      {completedSegs && completedSegs.length > 0 && (
        <div className="mb-3 space-y-2">
          {completedSegs.map((seg) => (
            <AgentSegmentBlock key={seg.id} segment={seg} isActive={false} defaultExpanded={false} />
          ))}
        </div>
      )}

      <div className="prose prose-sm dark:prose-invert max-w-none text-text-primary dark:text-text-primary-dark prose-headings:text-text-primary dark:prose-headings:text-text-primary-dark prose-a:text-accent prose-code:text-accent prose-pre:bg-surface dark:prose-pre:bg-bg-dark prose-pre:border prose-pre:border-border dark:prose-pre:border-border-dark">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
          {content}
        </ReactMarkdown>
      </div>
      {/* Copy button on hover */}
      <button
        onClick={handleCopy}
        className="absolute -bottom-7 left-0 p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
        aria-label="Copy response"
        title={copied ? '已复制' : '复制'}
      >
        {copied ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
    </div>
  );
}

export default memo(AssistantMessage);

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
        className="absolute -bottom-5 left-0 opacity-0 group-hover:opacity-100 transition-opacity text-xs text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark"
        aria-label="Copy response"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
}

export default memo(AssistantMessage);

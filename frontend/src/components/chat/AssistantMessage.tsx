'use client';

import { memo, useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { useStreamStore } from '@/stores/streamStore';
import { useConversationStore } from '@/stores/conversationStore';
import { PROSE_CLASSES } from '@/lib/styles';
import { markdownComponents } from '@/components/markdown';
import { getMessageEvents } from '@/lib/api';
import { reconstructSegments } from '@/lib/reconstructSegments';
import AgentSegmentBlock from './AgentSegmentBlock';
import ProcessingFlow from './ProcessingFlow';

interface AssistantMessageProps {
  content: string;
  messageId?: string;
  isSummarized?: boolean;
}

function AssistantMessage({ content, messageId, isSummarized }: AssistantMessageProps) {
  const [copied, setCopied] = useState(false);
  const completedSegs = useStreamStore(
    (s) => messageId ? s.completedSegments.get(messageId) : undefined
  );
  const conversationId = useConversationStore((s) => s.current?.id);

  // Lazy-load historical segments from persisted events when session cache is empty
  useEffect(() => {
    if (!messageId || !conversationId || completedSegs !== undefined) return;

    let cancelled = false;
    getMessageEvents(conversationId, messageId)
      .then((res) => {
        if (cancelled || res.events.length === 0) return;
        const segments = reconstructSegments(res.events);
        if (segments.length > 0) {
          const store = useStreamStore.getState();
          const newMap = new Map(store.completedSegments);
          newMap.set(messageId, segments);
          useStreamStore.setState({ completedSegments: newMap });
        }
      })
      .catch(() => {
        // Silently ignore — historical segments are non-critical
      });

    return () => { cancelled = true; };
  }, [messageId, conversationId, completedSegs]);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="group relative">
      {/* Completed execution segments (collapsible) */}
      {completedSegs && completedSegs.length > 0 && (
        <div className="mb-3">
          <ProcessingFlow segments={completedSegs} isActive={false} defaultExpanded={false}>
            {completedSegs.map((seg, idx) => (
              <AgentSegmentBlock key={seg.id} segment={seg} isActive={false} defaultExpanded={false} stepNumber={idx + 1} />
            ))}
          </ProcessingFlow>
        </div>
      )}

      <div className={PROSE_CLASSES}>
        {isSummarized && (
          <span className="inline-block mr-1 align-text-top" title="此消息已被压缩摘要">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-tertiary dark:text-text-tertiary-dark">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
              <path d="M3.27 6.96 12 12.01l8.73-5.05M12 22.08V12" />
            </svg>
          </span>
        )}
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={markdownComponents}>
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

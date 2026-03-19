'use client';

import { memo, useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { useStreamStore, interleaveFlowItems } from '@/stores/streamStore';
import { useConversationStore } from '@/stores/conversationStore';
import { PROSE_CLASSES } from '@/lib/styles';
import { markdownComponents } from '@/components/markdown';
import { getMessageEvents } from '@/lib/api';
import { reconstructSegments, reconstructNonAgentBlocks } from '@/lib/reconstructSegments';
import AgentSegmentBlock from './AgentSegmentBlock';
import InjectFlowBlock from './InjectFlowBlock';
import CompactionFlowBlock from './CompactionFlowBlock';
import ProcessingFlow from './ProcessingFlow';
import SummaryPopover from './SummaryPopover';

interface AssistantMessageProps {
  content: string;
  messageId?: string;
  responseSummary?: string | null;
}

function AssistantMessage({ content, messageId, responseSummary }: AssistantMessageProps) {
  const [copied, setCopied] = useState(false);
  const completedSegs = useStreamStore(
    (s) => messageId ? s.completedSegments.get(messageId) : undefined
  );
  const completedBlocks = useStreamStore(
    (s) => messageId ? s.completedNonAgentBlocks.get(messageId) : undefined
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
        const blocks = reconstructNonAgentBlocks(res.events);
        const store = useStreamStore.getState();
        if (segments.length > 0) {
          const newMap = new Map(store.completedSegments);
          newMap.set(messageId, segments);
          useStreamStore.setState({ completedSegments: newMap });
        }
        if (blocks.length > 0) {
          const nabMap = new Map(store.completedNonAgentBlocks);
          nabMap.set(messageId, blocks);
          useStreamStore.setState({ completedNonAgentBlocks: nabMap });
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

  const hasFlow = completedSegs && completedSegs.length > 0;
  const flowItems = hasFlow
    ? interleaveFlowItems(completedSegs, completedBlocks ?? [])
    : null;

  return (
    <div className="group relative">
      {/* Completed execution segments (collapsible) */}
      {flowItems && flowItems.length > 0 && (
        <div className="mb-3">
          <ProcessingFlow agentStepCount={completedSegs!.length} isActive={false} defaultExpanded={false}>
            {flowItems.map((item) => {
              if (item.kind === 'agent') {
                return (
                  <AgentSegmentBlock
                    key={item.segment.id}
                    segment={item.segment}
                    isActive={false}
                    defaultExpanded={false}
                    stepNumber={item.index + 1}
                  />
                );
              }
              if (item.kind === 'inject') {
                return <InjectFlowBlock key={item.id} content={item.content} />;
              }
              if (item.kind === 'compaction') {
                return <CompactionFlowBlock key={item.id} />;
              }
              return null;
            })}
          </ProcessingFlow>
        </div>
      )}

      <div className={PROSE_CLASSES}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={markdownComponents}>
          {content}
        </ReactMarkdown>
      </div>
      {/* Action bar on hover */}
      <div className="absolute -bottom-7 left-0 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={handleCopy}
          className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
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
        {responseSummary && <SummaryPopover summary={responseSummary} />}
      </div>
    </div>
  );
}

export default memo(AssistantMessage);

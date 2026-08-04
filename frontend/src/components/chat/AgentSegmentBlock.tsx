'use client';

import { memo, useState } from 'react';
import type { ExecutionSegment } from '@/stores/streamStore';
import { PROSE_CLASSES, MENU_ROW_HOVER } from '@/lib/styles';
import MarkdownBlock from '@/components/markdown/MarkdownBlock';
import { PillBadge } from '@/components/ui/PillBadge';
import ThinkingBlock from './ThinkingBlock';
import ToolCallCard from './ToolCallCard';

function formatArgumentChars(count: number): string {
  if (count < 1000) return `${count}`;
  return `${(count / 1000).toFixed(1)}k`;
}

interface AgentSegmentBlockProps {
  segment: ExecutionSegment;
  isActive: boolean;       // true = currently executing segment (last + isStreaming)
  defaultExpanded: boolean;
  stepNumber?: number;
}

function AgentSegmentBlock({ segment, isActive, defaultExpanded, stepNumber }: AgentSegmentBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const isExpanded = isActive || expanded;
  const hasBody = !!(
    segment.reasoningContent
    || segment.toolCalls.length > 0
    || segment.toolCallProgress.length > 0
    || segment.content
  );

  return (
    <div className="bg-chat dark:bg-chat-dark border border-border dark:border-border-dark rounded-card overflow-hidden">
      {/* Collapsible header */}
      <button
        onClick={() => { if (!isActive) setExpanded(!expanded); }}
        className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors ${
          isActive ? 'cursor-default' : `${MENU_ROW_HOVER} cursor-pointer`
        }`}
      >
        {/* Chevron */}
        {!isActive && (
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            className={`flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark transition-transform ${isExpanded ? 'rotate-90' : ''}`}
          >
            <path d="M4.5 2.5 8 6l-3.5 3.5" />
          </svg>
        )}

        {/* Agent badge inline */}
        <PillBadge
          tone={segment.status === 'running' ? 'accent' : 'neutral'}
          size="regular"
          className="gap-1.5"
        >
          {segment.status === 'running' && (
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          )}
          {segment.status === 'complete' && (
            <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" className="text-status-success">
              <path d="M2.5 6.5 5 9l4.5-6" />
            </svg>
          )}
          {segment.agent}
        </PillBadge>

        {/* Compact metadata — only shown when segment is done */}
        {segment.status === 'complete' && (segment.model || segment.tokenUsage || segment.llmDurationMs) && (
          <span className="ml-auto text-xs text-text-tertiary dark:text-text-tertiary-dark font-mono">
            {[
              segment.model,
              segment.tokenUsage && `${(segment.tokenUsage.input_tokens / 1000).toFixed(1)}k ↑ · ${(segment.tokenUsage.output_tokens / 1000).toFixed(1)}k ↓`,
              segment.llmDurationMs != null && `${(segment.llmDurationMs / 1000).toFixed(1)}s`,
            ].filter(Boolean).join(' · ')}
          </span>
        )}

      </button>

      {/* Body — always shown when active, togglable when collapsed */}
      {isExpanded && hasBody && (
        <div className="px-3 pb-3 space-y-3">
          {/* Thinking block */}
          {segment.reasoningContent && (() => {
            const isThinkingLive = isActive
              && !segment.content
              && segment.toolCalls.length === 0
              && segment.toolCallProgress.length === 0;
            return (
              <ThinkingBlock
                content={segment.reasoningContent}
                defaultExpanded={isThinkingLive}
                isLive={isThinkingLive}
              />
            );
          })()}

          {/* One segment is one native LLM invocation. Ordinary content may
              coexist with structured calls and is rendered exactly once. */}
          {segment.content && (
            <MarkdownBlock className={`${PROSE_CLASSES} ${isActive ? 'streaming-cursor' : ''}`}>
              {segment.content}
            </MarkdownBlock>
          )}

          {/* Tool calls */}
          {segment.toolCalls.map((tc) => (
            <ToolCallCard key={tc.id} toolCall={tc} />
          ))}

          {/* Native tool-call arguments may take a long time to stream.  Show a
              bounded liveness row; never render the incomplete JSON itself. */}
          {segment.toolCallProgress.map((progress) => (
            <div
              key={progress.callId ?? progress.index}
              className="flex items-center gap-2 pl-1 text-xs text-text-secondary dark:text-text-secondary-dark"
            >
              <span className="w-2 h-2 rounded-full bg-accent animate-pulse flex-shrink-0" />
              <span>{progress.status === 'generating' ? 'Preparing' : 'Waiting to run'}</span>
              <code className="font-mono text-text-primary dark:text-text-primary-dark">
                {progress.toolName || 'tool call'}
              </code>
              <span className="text-text-tertiary dark:text-text-tertiary-dark font-mono">
                · {formatArgumentChars(progress.argumentsChars)} chars
              </span>
            </div>
          ))}

        </div>
      )}
    </div>
  );
}

export default memo(AgentSegmentBlock);

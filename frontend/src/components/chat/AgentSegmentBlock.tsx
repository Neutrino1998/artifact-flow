'use client';

import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import type { ExecutionSegment } from '@/stores/streamStore';
import ThinkingBlock from './ThinkingBlock';
import ToolCallCard from './ToolCallCard';

/**
 * Strip complete and partial XML tool_call blocks from streaming content.
 * Complete: <tool_call>...</tool_call>
 * Partial (trailing): <tool_call>... (no closing tag yet)
 */
function stripToolCallXml(text: string): string {
  // Remove complete blocks
  let cleaned = text.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '');
  // Remove trailing partial block (opening tag without closing)
  cleaned = cleaned.replace(/<tool_call>[\s\S]*$/g, '');
  return cleaned.trimEnd();
}

interface AgentSegmentBlockProps {
  segment: ExecutionSegment;
  isActive: boolean;       // true = currently executing segment (last + isStreaming)
  defaultExpanded: boolean;
}

function AgentSegmentBlock({ segment, isActive, defaultExpanded }: AgentSegmentBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const isExpanded = isActive || expanded;
  const hasBody = !!(segment.reasoningContent || segment.toolCalls.length > 0 || segment.content);

  return (
    <div className="border border-border dark:border-border-dark rounded-card overflow-hidden">
      {/* Collapsible header */}
      <button
        onClick={() => { if (!isActive) setExpanded(!expanded); }}
        className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors ${
          isActive ? 'cursor-default' : 'hover:bg-bg dark:hover:bg-bg-dark cursor-pointer'
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
        <span
          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium ${
            segment.status === 'running'
              ? 'bg-accent/10 text-accent'
              : 'bg-bg dark:bg-bg-dark text-text-secondary dark:text-text-secondary-dark'
          }`}
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
        </span>
      </button>

      {/* Body — always shown when active, togglable when collapsed */}
      {isExpanded && hasBody && (
        <div className="px-3 pb-3 space-y-3">
          {/* Thinking block */}
          {segment.reasoningContent && (
            <ThinkingBlock
              content={segment.reasoningContent}
              defaultExpanded={segment.isThinking}
              isLive={segment.isThinking}
            />
          )}

          {/* Tool calls */}
          {segment.toolCalls.map((tc) => (
            <ToolCallCard key={tc.id} toolCall={tc} />
          ))}

          {/* Content — markdown when complete or active streaming */}
          {segment.content && (() => {
            const displayContent = stripToolCallXml(segment.content);
            if (!displayContent) return null;
            return (
              <div className={`prose prose-sm dark:prose-invert max-w-none text-text-primary dark:text-text-primary-dark prose-headings:text-text-primary dark:prose-headings:text-text-primary-dark prose-a:text-accent prose-code:text-accent prose-pre:bg-surface dark:prose-pre:bg-bg-dark prose-pre:border prose-pre:border-border dark:prose-pre:border-border-dark ${isActive ? 'streaming-cursor' : ''}`}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {displayContent}
                </ReactMarkdown>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

export default memo(AgentSegmentBlock);

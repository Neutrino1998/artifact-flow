'use client';

import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import type { ExecutionSegment } from '@/stores/streamStore';
import { PROSE_CLASSES } from '@/lib/styles';
import ThinkingBlock from './ThinkingBlock';
import AgentOutputBlock from './AgentOutputBlock';
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

/**
 * Extract only <tool_call> XML blocks (complete and partial) from text.
 * Inverse of stripToolCallXml — returns only the XML parts.
 */
function extractToolCallXml(text: string): string {
  const parts: string[] = [];
  for (const m of text.matchAll(/<tool_call>[\s\S]*?<\/tool_call>/g)) {
    parts.push(m[0]);
  }
  // Trailing partial block (opening without closing)
  const afterComplete = text.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '');
  const partial = afterComplete.match(/<tool_call>[\s\S]*$/);
  if (partial) parts.push(partial[0]);
  return parts.join('\n');
}

interface AgentSegmentBlockProps {
  segment: ExecutionSegment;
  isActive: boolean;       // true = currently executing segment (last + isStreaming)
  defaultExpanded: boolean;
}

function AgentSegmentBlock({ segment, isActive, defaultExpanded }: AgentSegmentBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const isExpanded = isActive || expanded;
  const hasBody = !!(segment.reasoningContent || segment.llmOutput || segment.toolCalls.length > 0 || segment.content);

  // --- Compute display values upfront ---
  // Whether this segment involves tool calls (past or in-progress)
  const hasTool = !!segment.llmOutput || segment.content.includes('<tool_call');

  // Source for pre-tool text and XML: prefer llmOutput (stable), fall back to streaming content
  const toolSource = segment.llmOutput || segment.content;

  // Pre-tool text: text before <tool_call>, rendered as markdown in a stable position.
  // When no tool calls, this is empty and mainContent handles everything.
  const preToolText = hasTool ? stripToolCallXml(toolSource) : '';

  // XML tool call blocks only (for AgentOutputBlock)
  const toolCallXml = hasTool ? extractToolCallXml(toolSource) : '';

  // Whether XML is currently being streamed (live indicator in AgentOutputBlock)
  const isStreamingXml = isActive && !segment.isThinking && !segment.llmOutput
    && segment.content.includes('<tool_call');

  // Main content: post-tool text from a new LLM round, or normal content when no tools involved.
  // - No tool calls: show content as-is
  // - Tool calls present but content still has XML (between LLM_COMPLETE and TOOL_START): empty (pre-tool text already shown above)
  // - Tool calls present, content cleared or has new text: show the new text
  let mainContent = '';
  if (hasTool) {
    if (segment.llmOutput && segment.content && !segment.content.includes('<tool_call')) {
      mainContent = segment.content;
    }
  } else {
    mainContent = segment.content;
  }

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
          {segment.reasoningContent && (() => {
            const isThinkingLive = isActive && !segment.content && !segment.llmOutput && segment.toolCalls.length === 0;
            return (
              <ThinkingBlock
                content={segment.reasoningContent}
                defaultExpanded={isThinkingLive}
                isLive={isThinkingLive}
              />
            );
          })()}

          {/* Pre-tool text — stable position; text before <tool_call> rendered as markdown.
              Appears in the same DOM slot whether sourced from streaming content or llmOutput,
              so new elements (AgentOutput, ToolCards) appear BELOW without layout shift. */}
          {preToolText && (
            <div className={PROSE_CLASSES}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                {preToolText}
              </ReactMarkdown>
            </div>
          )}

          {/* Agent Output — only XML tool_call blocks */}
          {toolCallXml && (
            <AgentOutputBlock content={toolCallXml} defaultExpanded={isStreamingXml} isLive={isStreamingXml} />
          )}

          {/* Tool calls */}
          {segment.toolCalls.map((tc) => (
            <ToolCallCard key={tc.id} toolCall={tc} />
          ))}

          {/* Main content — normal streaming text or post-tool text from new LLM round */}
          {mainContent && (
            <div className={`${PROSE_CLASSES} ${isActive ? 'streaming-cursor' : ''}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                {mainContent}
              </ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(AgentSegmentBlock);

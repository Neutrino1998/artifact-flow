'use client';

import { useStreamStore } from '@/stores/streamStore';
import AgentBadge from './AgentBadge';
import ThinkingBlock from './ThinkingBlock';
import ToolCallCard from './ToolCallCard';

export default function StreamingMessage() {
  const streamContent = useStreamStore((s) => s.streamContent);
  const currentAgent = useStreamStore((s) => s.currentAgent);
  const toolCalls = useStreamStore((s) => s.toolCalls);
  const reasoningContent = useStreamStore((s) => s.reasoningContent);
  const isThinking = useStreamStore((s) => s.isThinking);

  return (
    <div className="space-y-3">
      {/* Agent badge */}
      {currentAgent && <AgentBadge agent={currentAgent} status="running" />}

      {/* Thinking block — expanded while actively thinking, collapsed once content starts */}
      {reasoningContent && (
        <ThinkingBlock
          content={reasoningContent}
          defaultExpanded={isThinking}
          isLive={isThinking}
        />
      )}

      {/* Tool calls */}
      {toolCalls.map((tc) => (
        <ToolCallCard key={tc.id} toolCall={tc} />
      ))}

      {/* Streaming text — plain text with cursor during streaming */}
      {streamContent && (
        <div className="text-sm text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words streaming-cursor">
          {streamContent}
        </div>
      )}
    </div>
  );
}

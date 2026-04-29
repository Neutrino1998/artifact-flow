'use client';

import { useStreamStore, interleaveFlowItems } from '@/stores/streamStore';
import AgentSegmentBlock from './AgentSegmentBlock';
import InjectFlowBlock from './InjectFlowBlock';
import CompactionFlowBlock from './CompactionFlowBlock';
import ErrorFlowBlock from './ErrorFlowBlock';
import ProcessingFlow from './ProcessingFlow';

export default function StreamingMessage() {
  const segments = useStreamStore((s) => s.segments);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const nonAgentBlocks = useStreamStore((s) => s.nonAgentBlocks);
  const error = useStreamStore((s) => s.error);

  const flowItems = interleaveFlowItems(segments, nonAgentBlocks);

  if (flowItems.length === 0 && !error) return null;

  const agentStepCount = segments.length;

  // Total duration is only displayed via AssistantMessage after conversation
  // refresh — endStream() unmounts this component before isActive flips to false.
  return (
    <ProcessingFlow
      agentStepCount={agentStepCount}
      isActive={isStreaming}
      defaultExpanded={true}
      hasError={!!error}
    >
      {flowItems.map((item) => {
        if (item.kind === 'agent') {
          return (
            <AgentSegmentBlock
              key={item.segment.id}
              segment={item.segment}
              isActive={isStreaming && item.index === segments.length - 1}
              defaultExpanded={item.index === segments.length - 1}
              stepNumber={item.index + 1}
            />
          );
        }
        if (item.kind === 'inject') {
          return <InjectFlowBlock key={item.id} content={item.content} />;
        }
        if (item.kind === 'compaction') {
          return <CompactionFlowBlock key={item.id} block={item} />;
        }
        if (item.kind === 'error') {
          return <ErrorFlowBlock key={item.id} message={item.error} />;
        }
        return null;
      })}
      {/* Standalone fallback for non-event errors (e.g. SSE 401/404 transport
          failures that never reach the ERROR handler). When ERROR did fire,
          the block is already in nonAgentBlocks and rendered via interleave. */}
      {error && !nonAgentBlocks.some((b) => b.kind === 'error') && (
        <ErrorFlowBlock message={error} />
      )}
    </ProcessingFlow>
  );
}

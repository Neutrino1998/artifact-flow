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
  const executionMetrics = useStreamStore((s) => s.executionMetrics);

  const flowItems = interleaveFlowItems(segments, nonAgentBlocks);

  if (flowItems.length === 0 && !error) return null;

  const agentStepCount = segments.length;

  return (
    <ProcessingFlow
      agentStepCount={agentStepCount}
      isActive={isStreaming}
      defaultExpanded={true}
      hasError={!!error}
      totalDurationMs={executionMetrics?.total_duration_ms ?? null}
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
        return null;
      })}
      {error && <ErrorFlowBlock message={error} />}
    </ProcessingFlow>
  );
}

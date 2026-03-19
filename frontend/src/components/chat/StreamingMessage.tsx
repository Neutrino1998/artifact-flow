'use client';

import { useStreamStore, interleaveFlowItems } from '@/stores/streamStore';
import AgentSegmentBlock from './AgentSegmentBlock';
import InjectFlowBlock from './InjectFlowBlock';
import CompactionFlowBlock from './CompactionFlowBlock';
import ProcessingFlow from './ProcessingFlow';

export default function StreamingMessage() {
  const segments = useStreamStore((s) => s.segments);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const nonAgentBlocks = useStreamStore((s) => s.nonAgentBlocks);

  const flowItems = interleaveFlowItems(segments, nonAgentBlocks);

  if (flowItems.length === 0) return null;

  const agentStepCount = segments.length;

  return (
    <ProcessingFlow agentStepCount={agentStepCount} isActive={isStreaming} defaultExpanded={true}>
      {flowItems.map((item, idx) => {
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
          return <CompactionFlowBlock key={item.id} />;
        }
        return null;
      })}
    </ProcessingFlow>
  );
}

'use client';

import { useStreamStore } from '@/stores/streamStore';
import AgentSegmentBlock from './AgentSegmentBlock';
import ProcessingFlow from './ProcessingFlow';

export default function StreamingMessage() {
  const segments = useStreamStore((s) => s.segments);
  const isStreaming = useStreamStore((s) => s.isStreaming);

  if (segments.length === 0) return null;

  return (
    <ProcessingFlow segments={segments} isActive={isStreaming} defaultExpanded={true}>
      {segments.map((seg, idx) => (
        <AgentSegmentBlock
          key={seg.id}
          segment={seg}
          isActive={isStreaming && idx === segments.length - 1}
          defaultExpanded={idx === segments.length - 1}
          stepNumber={idx + 1}
        />
      ))}
    </ProcessingFlow>
  );
}

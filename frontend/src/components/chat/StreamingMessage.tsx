'use client';

import { useStreamStore } from '@/stores/streamStore';
import AgentSegmentBlock from './AgentSegmentBlock';
import InjectedMessage from './InjectedMessage';
import ProcessingFlow from './ProcessingFlow';

export default function StreamingMessage() {
  const segments = useStreamStore((s) => s.segments);
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const injectedMessages = useStreamStore((s) => s.injectedMessages);
  const compactionWait = useStreamStore((s) => s.compactionWait);

  if (segments.length === 0 && !compactionWait) return null;

  return (
    <>
      {/* Compaction wait indicator */}
      {compactionWait && (
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark flex items-center gap-1.5 px-3 py-1.5">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
            <path d="M3.27 6.96 12 12.01l8.73-5.05M12 22.08V12" />
          </svg>
          上下文压缩已完成，继续处理中...
        </div>
      )}

      {/* Injected messages — shown before segments */}
      {injectedMessages.map((msg, idx) => (
        <InjectedMessage key={`inject-${idx}`} content={msg.content} timestamp={msg.timestamp} />
      ))}

      {segments.length > 0 && (
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
      )}
    </>
  );
}

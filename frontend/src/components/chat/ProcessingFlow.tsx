'use client';

import { memo, useState, useEffect } from 'react';
import CyclingDots from './CyclingDots';

interface ProcessingFlowProps {
  agentStepCount: number;
  isActive: boolean;
  defaultExpanded: boolean;
  hasError?: boolean;
  /** Total turn duration in ms; only shown when not active. */
  totalDurationMs?: number | null;
  /** When set, the header shows a queued state instead of Processing/Error/Completed.
   *  Value = upper-bound count of tasks ahead in the concurrency queue. Step count
   *  is hidden (it's always 0 in this state). */
  queuedAhead?: number;
  children: React.ReactNode;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) {
    const tenths = Math.floor(ms / 100) / 10;
    return `${tenths.toFixed(1)}s`;
  }
  const m = Math.floor(totalSec / 60);
  const rem = totalSec - m * 60;
  return `${m}m ${rem}s`;
}

function ProcessingFlow({ agentStepCount, isActive, defaultExpanded, hasError, totalDurationMs, queuedAhead, children }: ProcessingFlowProps) {
  const isQueued = queuedAhead != null;
  const [expanded, setExpanded] = useState(defaultExpanded);

  useEffect(() => {
    setExpanded(isActive);
  }, [isActive]);

  return (
    <div>
      {/* Header row — inline disclosure style, no outer card chrome.
          In queued state the button is non-interactive (no body to reveal). */}
      <button
        onClick={() => !isQueued && setExpanded(!expanded)}
        className={`w-full flex items-center gap-2 py-1.5 px-2 text-xs transition-colors rounded-md ${isQueued ? 'cursor-default' : 'hover:bg-surface/60 dark:hover:bg-panel-accent-dark/60 cursor-pointer'}`}
      >
        {/* Chevron — hidden in queued state since there's nothing to expand */}
        {!isQueued && (
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            className={`flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark transition-transform ${expanded ? 'rotate-90' : ''}`}
          >
            <path d="M4.5 2.5 8 6l-3.5 3.5" />
          </svg>
        )}

        {/* Status indicator */}
        {isQueued ? (
          <span className="inline-flex items-center gap-1.5 text-text-secondary dark:text-text-secondary-dark font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary dark:bg-text-tertiary-dark animate-pulse" />
            服务当前已达到上限，请求排队中{queuedAhead! > 0 ? `（前面 ${queuedAhead} 个）` : ''}<CyclingDots />
          </span>
        ) : isActive ? (
          <span className="inline-flex items-center gap-1.5 text-accent font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            Processing<CyclingDots />
          </span>
        ) : hasError ? (
          <span className="inline-flex items-center gap-1.5 text-red-600 dark:text-red-400 font-medium">
            <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="6" cy="6" r="5" />
              <line x1="7.5" y1="4.5" x2="4.5" y2="7.5" />
              <line x1="4.5" y1="4.5" x2="7.5" y2="7.5" />
            </svg>
            Error
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-text-secondary dark:text-text-secondary-dark font-medium">
            <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" className="text-status-success">
              <path d="M2.5 6.5 5 9l4.5-6" />
            </svg>
            Completed
          </span>
        )}

        {/* Right side: step count + (when finished) total duration. Hidden in queued state
            (step count is always 0 until the engine actually starts). */}
        {!isQueued && (
          <span className="ml-auto text-text-tertiary dark:text-text-tertiary-dark">
            {agentStepCount} {agentStepCount === 1 ? 'step' : 'steps'}
            {!isActive && totalDurationMs != null && totalDurationMs > 0 && (
              <span className="ml-2 font-mono">· {formatDuration(totalDurationMs)}</span>
            )}
          </span>
        )}
      </button>

      {/* Body — agent segment list, connected by a vertical rail under the header chevron.
          Skipped in queued state: no segments yet, so the rail would render empty. */}
      {expanded && !isQueued && (
        <div className="relative pl-6 pt-1 pb-2">
          {/* Rail aligned with the header chevron's horizontal center (chevron at px-2 = x:8-20, center 14) */}
          <div className="absolute left-[13px] top-0 bottom-2 w-px bg-border dark:bg-border-dark" />
          {/* Hollow circle terminator at the bottom of the rail */}
          <div className="absolute left-[10px] bottom-0 w-2 h-2 rounded-full border border-border dark:border-border-dark" />
          <div className="relative space-y-2">
            {children}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(ProcessingFlow);

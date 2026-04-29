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

function ProcessingFlow({ agentStepCount, isActive, defaultExpanded, hasError, totalDurationMs, children }: ProcessingFlowProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  useEffect(() => {
    setExpanded(isActive);
  }, [isActive]);

  return (
    <div className="border border-border dark:border-border-dark rounded-card overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-bg dark:hover:bg-bg-dark cursor-pointer"
      >
        {/* Chevron */}
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

        {/* Status indicator */}
        {isActive ? (
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

        {/* Right side: step count + (when finished) total duration */}
        <span className="ml-auto text-text-tertiary dark:text-text-tertiary-dark">
          {agentStepCount} {agentStepCount === 1 ? 'step' : 'steps'}
          {!isActive && totalDurationMs != null && totalDurationMs > 0 && (
            <span className="ml-2 font-mono">· {formatDuration(totalDurationMs)}</span>
          )}
        </span>
      </button>

      {/* Body */}
      {expanded && (
        <div className="relative border-t border-border dark:border-border-dark px-3 py-3">
          {/* Vertical connector line — aligned with AgentSegmentBlock chevron center */}
          <div className="absolute left-[31px] top-5 bottom-5 w-px bg-border dark:bg-border-dark" />
          <div className="relative space-y-2">
            {children}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(ProcessingFlow);

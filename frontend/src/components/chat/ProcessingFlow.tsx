'use client';

import { memo, useState, useEffect } from 'react';
import CyclingDots from './CyclingDots';

interface ProcessingFlowProps {
  agentStepCount: number;
  isActive: boolean;
  defaultExpanded: boolean;
  hasError?: boolean;
  children: React.ReactNode;
}

function ProcessingFlow({ agentStepCount, isActive, defaultExpanded, hasError, children }: ProcessingFlowProps) {
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

        {/* Right side: step count */}
        <span className="ml-auto text-text-tertiary dark:text-text-tertiary-dark">
          {agentStepCount} {agentStepCount === 1 ? 'step' : 'steps'}
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

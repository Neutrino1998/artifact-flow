'use client';

import { memo, useState } from 'react';
import type { ToolCallInfo } from '@/stores/streamStore';

interface ToolCallCardProps {
  toolCall: ToolCallInfo;
}

function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const { toolName, agent, status, params, result, durationMs } = toolCall;

  const statusColor =
    status === 'running'
      ? 'text-accent'
      : status === 'success'
        ? 'text-status-success'
        : 'text-status-error';

  const statusIcon =
    status === 'running' ? (
      <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
    ) : status === 'success' ? (
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M2.5 6.5 5 9l4.5-6" />
      </svg>
    ) : (
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M3 3l6 6M9 3l-6 6" />
      </svg>
    );

  return (
    <div className="border border-border dark:border-border-dark rounded-card overflow-hidden text-xs">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-bg dark:hover:bg-bg-dark transition-colors"
      >
        <span className={`flex-shrink-0 ${statusColor}`}>{statusIcon}</span>
        <span className="font-medium text-text-primary dark:text-text-primary-dark">
          {toolName}
        </span>
        <span className="text-text-tertiary dark:text-text-tertiary-dark">
          by {agent}
        </span>
        {durationMs !== undefined && (
          <span className="ml-auto text-text-tertiary dark:text-text-tertiary-dark">
            {durationMs}ms
          </span>
        )}
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className={`transition-transform ${expanded ? 'rotate-90' : ''}`}
        >
          <path d="M4.5 2.5 8 6l-3.5 3.5" />
        </svg>
      </button>

      {/* Details */}
      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-border dark:border-border-dark">
          {/* Parameters */}
          {Object.keys(params).length > 0 && (
            <div className="pt-2">
              <div className="text-text-tertiary dark:text-text-tertiary-dark mb-1">
                Parameters
              </div>
              <pre className="bg-bg dark:bg-bg-dark rounded p-2 overflow-x-auto text-text-secondary dark:text-text-secondary-dark font-mono">
                {JSON.stringify(params, null, 2)}
              </pre>
            </div>
          )}

          {/* Result */}
          {result && (
            <div>
              <div className="text-text-tertiary dark:text-text-tertiary-dark mb-1">
                Result
              </div>
              <pre className="bg-bg dark:bg-bg-dark rounded p-2 overflow-x-auto text-text-secondary dark:text-text-secondary-dark font-mono max-h-48 overflow-y-auto">
                {result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(ToolCallCard);

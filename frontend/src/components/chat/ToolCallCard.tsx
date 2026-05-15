'use client';

import { memo } from 'react';
import type { ToolCallInfo } from '@/stores/streamStore';
import DisclosureRow from './DisclosureRow';

interface ToolCallCardProps {
  toolCall: ToolCallInfo;
}

function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const { toolName, agent, status, params, result, durationMs, permission } = toolCall;

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
    <DisclosureRow
      variant="inline"
      leading={<span className={`flex-shrink-0 ${statusColor}`}>{statusIcon}</span>}
      label={
        <>
          <span className="font-medium text-text-primary dark:text-text-primary-dark">{toolName}</span>
          <span className="text-text-tertiary dark:text-text-tertiary-dark">by {agent}</span>
        </>
      }
      bodyClassName="pl-5 pt-1 pb-2 space-y-2 text-xs"
    >
      {/* Parameters */}
      {Object.keys(params).length > 0 && (
        <div>
          <div className="text-text-tertiary dark:text-text-tertiary-dark mb-1">
            Parameters
          </div>
          <pre className="bg-panel-accent dark:bg-bg-dark rounded p-2 overflow-x-auto text-text-secondary dark:text-text-secondary-dark font-mono">
            {JSON.stringify(params, null, 2)}
          </pre>
        </div>
      )}

      {/* Permission — only set for CONFIRM-level tools */}
      {permission && (
        <div>
          <div className="text-text-tertiary dark:text-text-tertiary-dark mb-1">
            Permission
          </div>
          <div>
            <span className={`inline-flex items-center gap-1.5 ${permission.approved ? 'text-status-success' : 'text-status-error'}`}>
              {permission.approved ? (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M2.5 6.5 5 9l4.5-6" />
                </svg>
              ) : (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M3 3l6 6M9 3l-6 6" />
                </svg>
              )}
              {permission.approved ? 'approved' : 'denied'}
            </span>
            {permission.reason && (
              <span className="text-text-tertiary dark:text-text-tertiary-dark"> ({permission.reason})</span>
            )}
          </div>
        </div>
      )}

      {/* Result */}
      {result && (
        <div>
          <div className="flex items-center mb-1 text-text-tertiary dark:text-text-tertiary-dark">
            <span>Result</span>
            {durationMs !== undefined && (
              <span className="ml-auto font-mono">{durationMs}ms</span>
            )}
          </div>
          <pre className="bg-panel-accent dark:bg-bg-dark rounded p-2 overflow-x-auto text-text-secondary dark:text-text-secondary-dark font-mono max-h-48 overflow-y-auto">
            {result}
          </pre>
        </div>
      )}
    </DisclosureRow>
  );
}

export default memo(ToolCallCard);

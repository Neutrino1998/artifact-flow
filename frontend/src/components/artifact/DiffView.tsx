'use client';

import { memo } from 'react';

interface DiffViewProps {
  changes: [string, string][] | null;
}

function DiffView({ changes }: DiffViewProps) {
  if (!changes || changes.length === 0) {
    return (
      <div className="p-8 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
        No changes available for this version.
      </div>
    );
  }

  return (
    <div className="p-4 font-mono text-xs space-y-4">
      {changes.map(([oldText, newText], i) => (
        <div
          key={i}
          className="border border-border dark:border-border-dark rounded-lg overflow-hidden"
        >
          {/* Removed */}
          {oldText && (
            <div className="bg-red-50 dark:bg-red-950/20 border-b border-border dark:border-border-dark">
              <div className="px-3 py-0.5 text-[10px] text-status-error font-medium border-b border-border dark:border-border-dark">
                Removed
              </div>
              <pre className="px-3 py-2 text-status-error whitespace-pre-wrap break-all">
                {oldText}
              </pre>
            </div>
          )}
          {/* Added */}
          {newText && (
            <div className="bg-green-50 dark:bg-green-950/20">
              <div className="px-3 py-0.5 text-[10px] text-status-success font-medium border-b border-border dark:border-border-dark">
                Added
              </div>
              <pre className="px-3 py-2 text-status-success whitespace-pre-wrap break-all">
                {newText}
              </pre>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default memo(DiffView);

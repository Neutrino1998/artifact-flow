'use client';

import { memo } from 'react';

interface InjectedMessageProps {
  content: string;
  timestamp: string;
}

function InjectedMessage({ content }: InjectedMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[70%] bg-surface/60 dark:bg-surface-dark/60 rounded-bubble px-3 py-2 text-xs text-text-secondary dark:text-text-secondary-dark whitespace-pre-wrap break-words">
        <span className="text-text-tertiary dark:text-text-tertiary-dark mr-1">↳</span>
        {content}
      </div>
    </div>
  );
}

export default memo(InjectedMessage);

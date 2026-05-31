'use client';

import { memo } from 'react';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { CopyIcon } from '@/components/ui/CopyIcon';

interface ErrorFlowBlockProps {
  message?: string;
  /** 可回传错误码（req-xxxx）；存在时展示为可复制的定位码。 */
  requestId?: string;
}

function ErrorFlowBlock({ message, requestId }: ErrorFlowBlockProps) {
  const { copied, copy } = useCopyFeedback();
  // 有错误码时优先复制错误码(运维 grep 的对象);否则退回复制错误文本。
  const copyTarget = requestId ?? message;

  return (
    <div className="bg-chat dark:bg-chat-dark border border-red-500/40 rounded-card overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium bg-red-500/10 text-red-600 dark:text-red-400">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
          </svg>
          error
        </span>
        <div className="flex items-center gap-2 min-w-0">
          {requestId && (
            <code className="shrink-0 font-mono text-[11px] text-red-600/80 dark:text-red-400/80 truncate" title={requestId}>
              错误码 {requestId}
            </code>
          )}
          {copyTarget && (
            <button
              onClick={() => copy(copyTarget)}
              className="shrink-0 p-1 rounded text-red-600/70 dark:text-red-400/70 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-500/10"
              aria-label={requestId ? 'Copy error code' : 'Copy error'}
              title={copied ? '已复制' : requestId ? '复制错误码' : '复制'}
            >
              <CopyIcon copied={copied} />
            </button>
          )}
        </div>
      </div>
      {message && (
        <div className="px-3 pb-3 text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap break-words">
          {message}
        </div>
      )}
    </div>
  );
}

export default memo(ErrorFlowBlock);

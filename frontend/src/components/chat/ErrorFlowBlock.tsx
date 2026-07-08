'use client';

import { memo } from 'react';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { CopyIcon } from '@/components/ui/CopyIcon';
import { StatusNotice } from '@/components/ui/StatusNotice';

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
    <StatusNotice
      tone="error"
      title={
        <>
          <span>出错了</span>
          {requestId && (
            <code className="font-mono text-[11px] text-text-tertiary dark:text-text-tertiary-dark truncate" title={requestId}>
              错误码 {requestId}
            </code>
          )}
        </>
      }
      actions={
        copyTarget && (
          <button
            onClick={() => copy(copyTarget)}
            className="rounded-lg p-1 text-text-tertiary dark:text-text-tertiary-dark hover:bg-status-error/10 hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
            aria-label={requestId ? 'Copy error code' : 'Copy error'}
            title={copied ? '已复制' : requestId ? '复制错误码' : '复制'}
          >
            <CopyIcon copied={copied} />
          </button>
        )
      }
    >
      {message && (
        <div className="text-xs whitespace-pre-wrap break-words">
          {message}
        </div>
      )}
    </StatusNotice>
  );
}

export default memo(ErrorFlowBlock);

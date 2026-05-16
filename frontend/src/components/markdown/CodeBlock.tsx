'use client';

import { useRef, useCallback, type HTMLAttributes } from 'react';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { CopyIcon } from '@/components/ui/CopyIcon';

/**
 * Custom <pre> renderer for ReactMarkdown.
 * Wraps code blocks with a copy button in the top-right corner.
 */
export default function CodeBlock(props: HTMLAttributes<HTMLPreElement>) {
  const { copied, copy } = useCopyFeedback();
  const preRef = useRef<HTMLPreElement>(null);

  const handleCopy = useCallback(() => {
    copy(preRef.current?.textContent ?? '');
  }, [copy]);

  return (
    <div className="relative group/code">
      <pre ref={preRef} {...props} />
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 p-1.5 rounded-md opacity-0 group-hover/code:opacity-100 transition-opacity bg-surface/80 dark:bg-surface-dark/80 text-text-tertiary dark:text-text-tertiary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
        aria-label="Copy code"
        title={copied ? '已复制' : '复制代码'}
      >
        <CopyIcon copied={copied} />
      </button>
    </div>
  );
}

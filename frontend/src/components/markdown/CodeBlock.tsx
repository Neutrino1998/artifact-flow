'use client';

import { useState, useRef, useCallback, type HTMLAttributes } from 'react';

/**
 * Custom <pre> renderer for ReactMarkdown.
 * Wraps code blocks with a copy button in the top-right corner.
 */
export default function CodeBlock(props: HTMLAttributes<HTMLPreElement>) {
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  const handleCopy = useCallback(() => {
    const text = preRef.current?.textContent ?? '';
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, []);

  return (
    <div className="relative group/code">
      <pre ref={preRef} {...props} />
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 p-1.5 rounded-md opacity-0 group-hover/code:opacity-100 transition-opacity bg-surface/80 dark:bg-surface-dark/80 text-text-tertiary dark:text-text-tertiary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
        aria-label="Copy code"
        title={copied ? '已复制' : '复制代码'}
      >
        {copied ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
    </div>
  );
}

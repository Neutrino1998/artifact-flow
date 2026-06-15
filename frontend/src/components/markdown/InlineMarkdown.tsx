'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { markdownComponents, markdownUrlTransform } from './index';

/**
 * Compact inline markdown — for short one-liners like a tool-call `reason`.
 *
 * Renders **bold** / `code` / links properly (NOT wrapped in a code block), but
 * stays tight: no serif, no large prose margins. Deliberately NOT PROSE_CLASSES,
 * which is the heavy full-response style meant for whole answers.
 */
function InlineMarkdown({
  children,
  className = '',
}: {
  children: string;
  className?: string;
}) {
  return (
    <div
      className={`prose prose-sm dark:prose-invert max-w-none prose-p:my-0 prose-p:leading-snug prose-p:text-text-secondary dark:prose-p:text-text-secondary-dark text-text-secondary dark:text-text-secondary-dark ${className}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={markdownComponents}
        urlTransform={markdownUrlTransform}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export default memo(InlineMarkdown);

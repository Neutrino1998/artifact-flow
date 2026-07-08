'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import { PROSE_CLASSES } from '@/lib/styles';
import {
  markdownComponents,
  markdownComponentsWithDiagrams,
  markdownRehypePlugins,
  markdownRemarkPlugins,
  markdownUrlTransform,
} from './index';

interface MarkdownBlockProps {
  children: string;
  className?: string;
  diagrams?: boolean;
}

function MarkdownBlock({
  children,
  className = PROSE_CLASSES,
  diagrams = false,
}: MarkdownBlockProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={markdownRemarkPlugins}
        rehypePlugins={markdownRehypePlugins}
        components={diagrams ? markdownComponentsWithDiagrams : markdownComponents}
        urlTransform={markdownUrlTransform}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownBlock);

'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { PROSE_CLASSES } from '@/lib/styles';
import { markdownComponents } from '@/components/markdown';

interface MarkdownPreviewProps {
  content: string;
}

function MarkdownPreview({ content }: MarkdownPreviewProps) {
  return (
    <div className="p-5">
      <div className={PROSE_CLASSES}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={markdownComponents}>
          {content}
        </ReactMarkdown>
      </div>
    </div>
  );
}

export default memo(MarkdownPreview);

'use client';

import { memo } from 'react';
import MarkdownBlock from '@/components/markdown/MarkdownBlock';

interface MarkdownPreviewProps {
  content: string;
}

function MarkdownPreview({ content }: MarkdownPreviewProps) {
  return (
    <div className="p-5">
      <MarkdownBlock diagrams>{content}</MarkdownBlock>
    </div>
  );
}

export default memo(MarkdownPreview);

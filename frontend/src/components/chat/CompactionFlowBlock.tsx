'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import FlowBlock from './FlowBlock';
import { PROSE_CLASSES } from '@/lib/styles';
import { markdownComponents } from '@/components/markdown';
import type { CompactionBlock } from '@/stores/streamStore';

interface CompactionFlowBlockProps {
  block: CompactionBlock;
}

const PackageIcon = () => (
  <svg
    width="10"
    height="10"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
    <path d="M3.27 6.96 12 12.01l8.73-5.05M12 22.08V12" />
  </svg>
);

const CheckIcon = () => (
  <svg
    width="10"
    height="10"
    viewBox="0 0 12 12"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    className="text-status-success"
  >
    <path d="M2.5 6.5 5 9l4.5-6" />
  </svg>
);

function Badge({ state }: { state: CompactionBlock['state'] }) {
  if (state === 'running') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium bg-accent/10 text-accent">
        <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
        compaction
      </span>
    );
  }
  if (state === 'error') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium bg-status-error/10 text-status-error">
        <PackageIcon />
        compaction failed
      </span>
    );
  }
  // done
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium bg-bg dark:bg-transparent text-text-secondary dark:text-text-secondary-dark">
      <CheckIcon />
      compaction
    </span>
  );
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function Extra({ block }: { block: CompactionBlock }) {
  if (block.state === 'running') {
    if (!block.triggerTokens) return <>compressing…</>;
    const total = block.triggerTokens.input + block.triggerTokens.output;
    return <>compressing {formatTokens(total)} tokens…</>;
  }
  if (block.state === 'error') {
    return <>context truncated</>;
  }
  // done — mirror AgentSegmentBlock's header format 1:1
  //   {model} · {input}k ↑ · {output}k ↓ · {duration}s
  const parts: string[] = [];
  if (block.model) parts.push(block.model);
  if (block.tokenUsage) {
    parts.push(
      `${formatTokens(block.tokenUsage.input_tokens)} ↑ · ${formatTokens(block.tokenUsage.output_tokens)} ↓`
    );
  }
  if (block.durationMs != null) {
    parts.push(`${(block.durationMs / 1000).toFixed(1)}s`);
  }
  return <>{parts.join(' · ')}</>;
}

function Body({ block }: { block: CompactionBlock }) {
  if (block.state === 'error') {
    return (
      <div className="space-y-2">
        {block.error && (
          <div className="text-xs text-status-error font-mono whitespace-pre-wrap">
            {block.error}
          </div>
        )}
        {block.summary && (
          <div className="text-xs text-text-secondary dark:text-text-secondary-dark whitespace-pre-wrap">
            {block.summary}
          </div>
        )}
      </div>
    );
  }
  // done
  if (!block.summary) return null;
  return (
    <div className={PROSE_CLASSES}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={markdownComponents}
      >
        {block.summary}
      </ReactMarkdown>
    </div>
  );
}

function CompactionFlowBlock({ block }: CompactionFlowBlockProps) {
  const canToggle = block.state !== 'running';
  const body = canToggle ? <Body block={block} /> : undefined;

  return (
    <FlowBlock
      badge={<Badge state={block.state} />}
      extra={<Extra block={block} />}
      body={body}
      canToggle={canToggle}
    />
  );
}

export default memo(CompactionFlowBlock);

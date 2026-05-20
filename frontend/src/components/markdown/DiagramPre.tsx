'use client';

import { Children, isValidElement, type HTMLAttributes, type ReactNode } from 'react';
import CodeBlock from './CodeBlock';
import MermaidBlock from './MermaidBlock';

type DiagramPreProps = HTMLAttributes<HTMLPreElement> & { node?: unknown };

function nodeText(node: ReactNode): string {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(nodeText).join('');
  if (isValidElement(node)) {
    return nodeText((node.props as { children?: ReactNode }).children);
  }
  return '';
}

/**
 * <pre> renderer that swaps a ```mermaid block for a rendered diagram, and
 * defers everything else to the normal copy-button CodeBlock.
 *
 * Detection is at the <pre> level (not <code>) so the whole code block is
 * replaced by the diagram — no leftover copy-button wrapper or <pre> shell.
 * Inline code never hits this path (it has no <pre>), so only fenced blocks
 * are considered.
 *
 * Only used by markdownComponentsWithDiagrams (artifact preview + final
 * response) — the streaming flow keeps the plain CodeBlock so half-written
 * mermaid never flickers a parse error.
 */
export default function DiagramPre({ node: _node, ...props }: DiagramPreProps) {
  const child = Children.toArray(props.children).find(isValidElement);
  const className = (child?.props as { className?: string } | undefined)?.className ?? '';

  if (/\blanguage-mermaid\b/.test(className)) {
    return <MermaidBlock code={nodeText(props.children).replace(/\n$/, '')} />;
  }

  return <CodeBlock {...props} />;
}

'use client';

import { useEffect, useId, useRef, useState, useCallback } from 'react';
import { useUIStore } from '@/stores/uiStore';
import ErrorFlowBlock from '@/components/chat/ErrorFlowBlock';

interface MermaidBlockProps {
  code: string;
}

// Fit the diagram into a bounding box. mermaid pins the SVG's inline
// `max-width` to the diagram's intrinsic width, so we rewrite that inline
// style:
//   - width grows at most UPSCALE_FACTOR× the intrinsic width (a tiny graph
//     enlarges modestly instead of being blown up to the full cap), then is
//     hard-capped at MAX_WIDTH_PX.
//   - max-height bounds runaway-tall diagrams (e.g. long vertical flowcharts);
//     when hit, the SVG's preserveAspectRatio shrinks the content to fit and
//     centers it — no distortion.
const MAX_WIDTH_PX = 640;
const MAX_HEIGHT_PX = MAX_WIDTH_PX * 1.2; // 768 — keeps tall diagrams compact without squashing them
const UPSCALE_FACTOR = 1.5;

function constrainSize(svg: string): string {
  return svg.replace(/max-width:\s*([\d.]+)px;?/, (_m, w) => {
    const grown = (parseFloat(w) * UPSCALE_FACTOR).toFixed(1);
    return `max-width: min(${grown}px, ${MAX_WIDTH_PX}px); max-height: ${MAX_HEIGHT_PX}px; height: auto;`;
  });
}

/**
 * Renders a ```mermaid fenced block as an SVG diagram.
 *
 * Only mounted in non-streaming surfaces (artifact preview + final response),
 * so `code` is always a complete, stable block — no partial-parse guarding
 * needed. Invalid syntax (LLM can still emit it) falls back to showing source.
 *
 * mermaid is dynamically imported inside the effect: it touches `document` at
 * load time (breaks Next.js SSR) and is heavy (~500KB), so this both confines
 * it to the browser and code-splits it out of the initial bundle.
 */
export default function MermaidBlock({ code }: MermaidBlockProps) {
  const theme = useUIStore((s) => s.theme);
  const isDark = theme === 'dark';
  const containerRef = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState('');
  const [error, setError] = useState<string | null>(null);
  // mermaid.render(id) injects a temp DOM node it locates via querySelector,
  // so the id must be selector-safe — useId() returns colons, strip them.
  const renderId = `mermaid-${useId().replace(/[:]/g, '')}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: isDark ? 'dark' : 'default',
          securityLevel: 'strict',
          // On a parse error mermaid otherwise draws its "bomb" error SVG and
          // appends it to document.body (orphaned at the page bottom) before
          // throwing. This makes it call removeTempElements() and rethrow
          // instead, so our catch below owns the fallback (show source).
          suppressErrorRendering: true,
        });
        const { svg: out } = await mermaid.render(renderId, code);
        if (!cancelled) {
          setSvg(constrainSize(out));
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setSvg('');
          // mermaid throws a string-ish error; .message usually carries the
          // "Parse error on line N" detail. Fall back to a generic label.
          const msg = e instanceof Error ? e.message : String(e);
          setError(msg.trim() || '未知错误');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [code, isDark, renderId]);

  const downloadSvg = useCallback(() => {
    const svgEl = containerRef.current?.querySelector('svg');
    if (!svgEl) return;
    // SVG export instead of PNG: mermaid embeds <foreignObject> HTML labels,
    // which taint the canvas (toBlob throws) on both Chrome and Safari, so
    // canvas rasterization is a dead end. Serializing the SVG sidesteps the
    // canvas entirely — works in every browser and stays vector-quality.
    const serialized = new XMLSerializer().serializeToString(svgEl);
    const doc = `<?xml version="1.0" encoding="UTF-8"?>\n${serialized}`;
    const url = URL.createObjectURL(new Blob([doc], { type: 'image/svg+xml;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'diagram.svg';
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  if (error !== null) {
    return (
      <div className="my-2 space-y-1">
        <div className="text-xs text-status-error">图表渲染失败,显示源码:</div>
        <pre><code className="language-mermaid">{code}</code></pre>
        <ErrorFlowBlock message={error} />
      </div>
    );
  }

  return (
    <div className="relative group/mermaid my-2 rounded-card border border-border dark:border-border-dark bg-chat dark:bg-chat-dark p-3">
      <div
        ref={containerRef}
        className="w-full [&>svg]:block [&>svg]:mx-auto"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      {svg && (
        <button
          onClick={downloadSvg}
          className="absolute top-2 right-2 p-1.5 rounded-md opacity-0 group-hover/mermaid:opacity-100 focus:opacity-100 [@media(pointer:coarse)]:opacity-100 transition-opacity bg-surface/80 dark:bg-surface-dark/80 text-text-tertiary dark:text-text-tertiary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
          aria-label="Download SVG"
          title="下载 SVG"
        >
          <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <path d="M7 10l5 5 5-5" />
            <path d="M12 15V3" />
          </svg>
        </button>
      )}
    </div>
  );
}

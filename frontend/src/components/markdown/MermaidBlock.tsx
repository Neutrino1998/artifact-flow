'use client';

import { useEffect, useId, useRef, useState, useCallback } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { triggerBlobDownload } from '@/lib/download';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { CopyIcon } from '@/components/ui/CopyIcon';
import FullscreenViewer, { ViewerToolbarButton } from '@/components/ui/FullscreenViewer';
import ZoomableCanvas from '@/components/ui/ZoomableCanvas';
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

function svgDimensions(svg: string): { width: number; height: number } {
  const viewBox = svg.match(
    /viewBox=["']\s*[+-]?[\d.]+\s+[+-]?[\d.]+\s+([\d.]+)\s+([\d.]+)\s*["']/i,
  );
  if (viewBox) {
    const width = Number.parseFloat(viewBox[1]);
    const height = Number.parseFloat(viewBox[2]);
    if (width > 0 && height > 0) return { width, height };
  }

  // Mermaid normally emits a viewBox. Keep malformed/older output expandable
  // with a conservative fallback derived from the intrinsic max-width.
  const intrinsicWidth = Number.parseFloat(
    svg.match(/max-width:\s*([\d.]+)px/i)?.[1] ?? '',
  );
  const width = intrinsicWidth > 0 ? intrinsicWidth : MAX_WIDTH_PX;
  return { width, height: width * 0.75 };
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
  const { copied, copy } = useCopyFeedback();
  const inlineRef = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [inlineHeight, setInlineHeight] = useState(0);
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
          setSvg(out);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setSvg('');
          setExpanded(false);
          // mermaid throws a string-ish error; .message usually carries the
          // "Parse error on line N" detail. Fall back to a generic label.
          const msg = e instanceof Error ? e.message : String(e);
          setError(msg.trim() || '未知错误');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [code, isDark, renderId]);

  const copySource = useCallback(() => {
    copy(code);
  }, [code, copy]);

  const openViewer = useCallback(() => {
    setInlineHeight(inlineRef.current?.getBoundingClientRect().height ?? 0);
    setExpanded(true);
  }, []);

  const downloadSvg = useCallback(() => {
    // SVG export instead of PNG: mermaid embeds <foreignObject> HTML labels,
    // which taint the canvas (toBlob throws) on both Chrome and Safari, so
    // canvas rasterization is a dead end. Downloading Mermaid's original SVG
    // sidesteps the canvas and avoids baking the inline preview's 640px cap into
    // the exported vector.
    if (!svg) return;
    const doc = `<?xml version="1.0" encoding="UTF-8"?>\n${svg}`;
    triggerBlobDownload(
      'diagram.svg',
      new Blob([doc], { type: 'image/svg+xml;charset=utf-8' }),
    );
  }, [svg]);

  const dimensions = svgDimensions(svg);

  if (error !== null) {
    return (
      <div className="my-2 space-y-1">
        <div className="text-xs text-status-error">图表渲染失败，显示源码：</div>
        <pre><code className="language-mermaid">{code}</code></pre>
        <ErrorFlowBlock message={error} />
      </div>
    );
  }

  return (
    <div className="relative group/mermaid my-2 rounded-card border border-border dark:border-border-dark bg-chat dark:bg-chat-dark p-3">
      {/* The inline and fullscreen copies are mutually exclusive. Mermaid SVGs
          contain document-scoped marker/clip IDs, so injecting both at once can
          resolve the viewer's references against the hidden inline copy. */}
      {expanded ? (
        <div aria-hidden="true" style={{ height: inlineHeight }} />
      ) : (
        <div
          ref={inlineRef}
          className="w-full [&>svg]:block [&>svg]:mx-auto"
          dangerouslySetInnerHTML={{ __html: constrainSize(svg) }}
        />
      )}
      {svg && (
        <div className="absolute top-2 right-2 flex items-center gap-1 opacity-0 group-hover/mermaid:opacity-100 focus-within:opacity-100 [@media(pointer:coarse)]:opacity-100 transition-opacity">
          <button
            type="button"
            onClick={openViewer}
            className="p-1.5 rounded-md bg-surface/80 dark:bg-surface-dark/80 text-text-tertiary dark:text-text-tertiary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
            aria-label="Expand Mermaid diagram"
            title="展开图表"
          >
            <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" />
            </svg>
          </button>
          <button
            type="button"
            onClick={copySource}
            className="p-1.5 rounded-md bg-surface/80 dark:bg-surface-dark/80 text-text-tertiary dark:text-text-tertiary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
            aria-label="Copy Mermaid source"
            title={copied ? '已复制' : '复制 Mermaid 源码'}
          >
            <CopyIcon copied={copied} />
          </button>
          <button
            type="button"
            onClick={downloadSvg}
            className="p-1.5 rounded-md bg-surface/80 dark:bg-surface-dark/80 text-text-tertiary dark:text-text-tertiary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
            aria-label="Download SVG"
            title="下载 SVG"
          >
            <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <path d="M7 10l5 5 5-5" />
              <path d="M12 15V3" />
            </svg>
          </button>
        </div>
      )}

      {svg && (
        <FullscreenViewer
          open={expanded}
          title="Mermaid 图表"
          onClose={() => setExpanded(false)}
          toolbarActions={(
            <>
              <ViewerToolbarButton
                onClick={copySource}
                aria-label="Copy Mermaid source"
                title={copied ? '已复制' : '复制 Mermaid 源码'}
              >
                <CopyIcon copied={copied} />
              </ViewerToolbarButton>
              <ViewerToolbarButton
                onClick={downloadSvg}
                aria-label="Download SVG"
                title="下载 SVG"
              >
                <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <path d="M7 10l5 5 5-5M12 15V3" />
                </svg>
              </ViewerToolbarButton>
            </>
          )}
        >
          <ZoomableCanvas
            contentWidth={dimensions.width}
            contentHeight={dimensions.height}
            resetKey={svg}
            onBackgroundClick={() => setExpanded(false)}
            ariaLabel="Mermaid diagram viewer"
          >
            <div
              className="h-full w-full [&>svg]:!block [&>svg]:!h-full [&>svg]:!w-full [&>svg]:!max-h-none [&>svg]:!max-w-none"
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          </ZoomableCanvas>
        </FullscreenViewer>
      )}
    </div>
  );
}

'use client';

import { useEffect, useRef, useState } from 'react';
import { fetchArtifactRawBlob } from '@/lib/api';
import { useArtifactStore } from '@/stores/artifactStore';
import BinaryFilePreview from './BinaryFilePreview';

const MAX_PREVIEW_BYTES = 15 * 1024 * 1024;
const FRAME_SRC_DOC = '<!doctype html><html><head><base target="_blank"></head><body></body></html>';
const FRAME_STYLE_ID = 'artifact-docx-preview-style';

function installFrameStyles(doc: Document) {
  doc.getElementById(FRAME_STYLE_ID)?.remove();
  const style = doc.createElement('style');
  style.id = FRAME_STYLE_ID;
  style.textContent = `
    html, body {
      margin: 0;
      min-height: 100%;
      background: #f3f4f6;
      color: #111827;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body {
      box-sizing: border-box;
    }
    a {
      color: inherit;
      text-decoration: underline;
    }
    .docx-wrapper {
      background: transparent !important;
      box-sizing: border-box !important;
      display: block !important;
      min-height: 100% !important;
      padding: 16px !important;
    }
    .docx-wrapper > section.docx {
      background: #fff !important;
      box-shadow: 0 8px 28px rgba(15, 23, 42, 0.16) !important;
      height: auto !important;
      margin: 0 auto 16px auto !important;
      min-height: 0 !important;
      max-width: 820px !important;
      overflow: visible !important;
      width: 100% !important;
    }
    .docx-wrapper > section.docx > article {
      margin-bottom: 0 !important;
    }
    .docx-wrapper header,
    .docx-wrapper footer {
      display: none !important;
    }
    .docx {
      overflow-wrap: break-word;
    }
    .docx img,
    .docx svg {
      height: auto;
      max-width: 100%;
    }
    .docx table {
      max-width: 100%;
    }
  `;
  doc.head.appendChild(style);
}

function resetFrame(doc: Document) {
  doc.head.innerHTML = `
    <base target="_blank">
  `;
  installFrameStyles(doc);
  doc.body.innerHTML = '';
}

export default function DocxPreview({
  sessionId,
  artifactId,
  originalFilename,
  contentType,
}: {
  sessionId: string;
  artifactId: string;
  originalFilename?: string | null;
  contentType: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const pendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);
  const [frameReady, setFrameReady] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (pendingFlush || frameReady === 0) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchArtifactRawBlob(sessionId, artifactId)
      .then(async (blob) => {
        if (blob.size > MAX_PREVIEW_BYTES) {
          throw new Error('Word 文件较大，已跳过浏览器内预览');
        }

        const doc = iframeRef.current?.contentDocument;
        if (!doc) throw new Error('Word 预览容器未就绪');
        resetFrame(doc);

        const { renderAsync } = await import('docx-preview');
        await renderAsync(blob, doc.body, doc.head, {
          // docx-preview does not calculate Word-style natural page breaks. Use
          // a continuous flow so long documents remain readable instead of
          // showing misleading page shells or stale page numbers.
          breakPages: false,
          ignoreHeight: true,
          ignoreLastRenderedPageBreak: true,
          ignoreWidth: true,
          renderHeaders: false,
          renderFooters: false,
          renderComments: true,
          renderChanges: true,
          renderAltChunks: false,
          renderFootnotes: true,
          renderEndnotes: true,
          useBase64URL: true,
        });
        installFrameStyles(doc);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Word 预览失败，可下载原件查看');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [artifactId, frameReady, pendingFlush, sessionId]);

  if (pendingFlush || error) {
    return (
      <BinaryFilePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        description={error ?? 'Word 原件将在本回合完成后可预览'}
        pendingMessage="本回合完成后可预览或下载原件"
      />
    );
  }

  return (
    <div className="relative h-full bg-bg dark:bg-bg-dark">
      <iframe
        ref={iframeRef}
        title="Word preview"
        sandbox="allow-same-origin"
        referrerPolicy="no-referrer"
        srcDoc={FRAME_SRC_DOC}
        onLoad={() => setFrameReady((n) => n + 1)}
        className="block h-full w-full border-0 bg-white"
      />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-chat/80 text-sm text-text-tertiary backdrop-blur-sm dark:bg-chat-dark/80 dark:text-text-tertiary-dark">
          正在渲染 Word...
        </div>
      )}
    </div>
  );
}

'use client';

import { useCallback, useState } from 'react';
import { fetchArtifactRawObjectUrl } from '@/lib/api';
import { useArtifactStore } from '@/stores/artifactStore';

/** Render a non-image blob-backed artifact (docx / pdf upload — C-0 blob-only:
 *  rich formats have no text representation; reading/converting them is a
 *  sandbox capability). The panel shows a file card with a raw download.
 *
 *  Mirrors ImagePreview's live-turn handling: while the artifact is live THIS
 *  turn (pendingFlush) the blob isn't flushed yet → /raw would 404, so the
 *  download is replaced by a pending hint. COMPLETE clears liveContent and
 *  re-renders us with the download enabled. */
export default function BinaryFilePreview({
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
  const [error, setError] = useState<string | null>(null);
  // Live this turn, not yet flushed. Cleared at COMPLETE.
  const pendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);

  const handleDownload = useCallback(async () => {
    setError(null);
    try {
      const url = await fetchArtifactRawObjectUrl(sessionId, artifactId);
      const a = document.createElement('a');
      a.href = url;
      a.download = originalFilename ?? artifactId;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // Generic, user-facing — the raw error + request id are logged server-side.
      setError('下载失败，请稍后重试');
    }
  }, [sessionId, artifactId, originalFilename]);

  return (
    <div className="h-full flex items-center justify-center p-6">
      <div className="flex flex-col items-center gap-3 text-center max-w-sm">
        <svg
          width="40"
          height="40"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className="text-text-tertiary dark:text-text-tertiary-dark"
        >
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
        </svg>
        <div className="text-sm text-text-primary dark:text-text-primary-dark break-all">
          {originalFilename ?? artifactId}
        </div>
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark break-all">
          {contentType} · 二进制文件，无文本预览
        </div>
        {pendingFlush ? (
          <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
            本回合完成后可下载原件
          </div>
        ) : (
          <button
            onClick={handleDownload}
            className="px-3 py-1.5 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors"
          >
            下载原件
          </button>
        )}
        {error && (
          <div className="text-xs text-red-500">{error}</div>
        )}
      </div>
    </div>
  );
}

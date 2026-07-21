'use client';

import { useCallback, useEffect, useState } from 'react';
import { fetchArtifactRawObjectUrl, type ArtifactRawObjectUrlFetcher } from '@/lib/api';
import { triggerObjectUrlDownload } from '@/lib/download';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStreamStore } from '@/stores/streamStore';
import FullscreenViewer, { ViewerToolbarButton } from '@/components/ui/FullscreenViewer';
import ZoomableCanvas from '@/components/ui/ZoomableCanvas';

/** Render an image artifact (content_type image/*). Source depends on whether the
 *  artifact is live THIS turn (pendingFlush = liveContent[id], cleared at COMPLETE):
 *
 *  - Live this turn:
 *      · user upload → the send-local preview File (artifactStore.localPreviews,
 *        instant, no fetch); matched by name, which is unique per turn (composer
 *        dedups, backend echoes it as original_filename), so it can't bind to the
 *        wrong upload. The cache shares liveContent's lifecycle (cleared at
 *        COMPLETE), so a later turn's same-named upload can't shadow it.
 *      · tool/model-generated (no local copy) → "being loaded" hint, NOT an error
 *        (blob isn't flushed yet → /raw would 404). COMPLETE re-runs us → /raw.
 *  - Settled (past-turn, or post-COMPLETE) → authed /raw fetch (an <img src> can't
 *    carry the JWT) → object URL. Never uses a local preview — a same-named file
 *    sent for a later turn must not shadow this artifact's own DB blob.
 *
 *  The user never sees a raw backend error mid-turn; a real failure shows a clean
 *  generic message — the detailed error + request id stay in the server log. */
export default function ImagePreview({
  sessionId,
  artifactId,
  originalFilename,
  refreshKey,
  fetchRawObjectUrl = fetchArtifactRawObjectUrl,
  pendingFlush: pendingFlushProp,
  useLocalPreview = true,
}: {
  sessionId: string;
  artifactId: string;
  originalFilename?: string | null;
  refreshKey?: string;
  fetchRawObjectUrl?: ArtifactRawObjectUrlFetcher;
  pendingFlush?: boolean;
  useLocalPreview?: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [dimensions, setDimensions] = useState({ width: 1, height: 1 });

  // The send-local preview File for this upload, matched by original name (stable
  // ref → only re-renders when it appears/disappears). undefined for any image not
  // uploaded this turn (model/tool-generated, or a past-turn artifact).
  const localFile = useArtifactStore((s) =>
    useLocalPreview && originalFilename ? s.localPreviews[originalFilename] : undefined
  );
  // Live this turn, not yet flushed (created/updated this turn). Cleared at COMPLETE.
  const storePendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);
  const pendingFlush = pendingFlushProp ?? storePendingFlush;
  const isStreaming = useStreamStore((s) => s.isStreaming);

  useEffect(() => {
    setExpanded(false);
    setDimensions({ width: 1, height: 1 });
  }, [artifactId]);

  useEffect(() => {
    setUrl(null);
    setError(null);

    // Live this turn (created/updated, blob not yet flushed). The local-preview
    // fallback is scoped to THIS branch deliberately: a settled / past-turn
    // artifact must read its OWN DB blob, never a same-named preview from a
    // *later* turn (cross-turn duplicate name → wrong image). Cleared at COMPLETE,
    // which flips pendingFlush false + refreshKey → re-run → /raw.
    if (pendingFlush) {
      if (localFile) {
        const objectUrl = URL.createObjectURL(localFile);
        setUrl(objectUrl);
        return () => URL.revokeObjectURL(objectUrl);
      }
      // Fresh non-upload image (tool/model-generated): no local copy and blob not
      // flushed → /raw would 404. Show the pending hint below, not an error.
      return;
    }

    if (!sessionId) {
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    fetchRawObjectUrl(sessionId, artifactId)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        // Generic, user-facing — the raw error + request id are logged server-side.
        if (!cancelled) setError('图片加载失败');
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifactId, fetchRawObjectUrl, localFile, pendingFlush, refreshKey, sessionId]);

  const handleDownload = useCallback(async () => {
    if (!sessionId || pendingFlush || isStreaming) return;
    try {
      // Fetch a dedicated URL for download: triggerObjectUrlDownload revokes its
      // input after clicking, so passing the displayed URL would break both the
      // inline image and the still-open viewer.
      const downloadUrl = await fetchRawObjectUrl(sessionId, artifactId);
      triggerObjectUrlDownload(originalFilename || artifactId, downloadUrl);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Download failed';
      window.alert(message);
    }
  }, [artifactId, fetchRawObjectUrl, isStreaming, originalFilename, pendingFlush, sessionId]);

  if (error) {
    return (
      <div className="h-full flex items-center justify-center p-6 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
        {error}
      </div>
    );
  }
  if (!url) {
    return (
      <div className="h-full flex items-center justify-center text-text-tertiary dark:text-text-tertiary-dark">
        {pendingFlush ? '图片加载中，完成后显示…' : '加载图片中…'}
      </div>
    );
  }
  const title = originalFilename || artifactId;
  return (
    <div className="h-full overflow-auto flex items-start justify-center p-4">
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="group/image relative max-w-full cursor-zoom-in rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 dark:focus-visible:ring-offset-bg-dark"
        aria-label={`全屏查看图片：${title}`}
        title="点击全屏查看"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt={title}
          className="max-w-full h-auto"
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            if (naturalWidth > 0 && naturalHeight > 0) {
              setDimensions({ width: naturalWidth, height: naturalHeight });
            }
          }}
        />
        <span className="pointer-events-none absolute right-2 top-2 flex h-8 w-8 items-center justify-center rounded-full bg-surface/80 dark:bg-surface-dark/80 text-text-secondary dark:text-text-secondary-dark opacity-0 group-hover/image:opacity-100 group-focus-visible/image:opacity-100 [@media(pointer:coarse)]:opacity-100 shadow-float transition-opacity">
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" />
          </svg>
        </span>
      </button>

      <FullscreenViewer
        open={expanded}
        title={title}
        onClose={() => setExpanded(false)}
        toolbarActions={!pendingFlush && !isStreaming ? (
          <ViewerToolbarButton
            onClick={handleDownload}
            aria-label="Download image"
            title="下载原图"
          >
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <path d="M7 10l5 5 5-5M12 15V3" />
            </svg>
          </ViewerToolbarButton>
        ) : undefined}
      >
        <ZoomableCanvas
          contentWidth={dimensions.width}
          contentHeight={dimensions.height}
          resetKey={`${artifactId}:${url}`}
          onBackgroundClick={() => setExpanded(false)}
          ariaLabel={`图片查看器：${title}`}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={title}
            draggable={false}
            className="h-full w-full select-none object-contain"
          />
        </ZoomableCanvas>
      </FullscreenViewer>
    </div>
  );
}

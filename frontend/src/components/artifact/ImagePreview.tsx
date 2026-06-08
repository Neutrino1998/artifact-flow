'use client';

import { useEffect, useState } from 'react';
import { fetchArtifactRawObjectUrl } from '@/lib/api';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStagedFilesStore } from '@/stores/stagedFilesStore';

/** Render an image artifact (content_type image/*). Three sources, in order:
 *
 *  1. Staged File (user upload, this turn) — the File is still in the composer
 *     until the turn's terminal resolves; render it locally (instant, no fetch).
 *  2. Pending flush (any image live this turn with no local copy — e.g. a
 *     tool/model-generated image): the blob has no durable home until flush_all
 *     at COMPLETE, so /raw would 404. Show a "being saved" hint, NOT an error —
 *     liveContent[id] is the signal (cleared at COMPLETE), at which point we re-run.
 *  3. DB blob — authed /raw fetch (an <img src> can't carry the JWT) → object URL.
 *
 *  The user never sees a raw backend error during a normal mid-turn image; a real
 *  failure (post-turn, genuinely missing) shows a clean generic message — the
 *  detailed error + request id stay in the server log, not the preview pane. */
export default function ImagePreview({
  sessionId,
  artifactId,
  originalFilename,
  refreshKey,
}: {
  sessionId: string;
  artifactId: string;
  originalFilename?: string | null;
  refreshKey?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The staged File for this upload, matched by original name (stable ref → only
  // re-renders when it appears/disappears). undefined for any image not uploaded
  // this turn (model/tool-generated, or a past-turn artifact).
  const localFile = useStagedFilesStore((s) =>
    originalFilename ? s.files.find((f) => f.file.name === originalFilename)?.file : undefined
  );
  // Live this turn, not yet flushed (created/updated this turn). Cleared at COMPLETE.
  const pendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);

  useEffect(() => {
    setUrl(null);
    setError(null);

    if (localFile) {
      const objectUrl = URL.createObjectURL(localFile);
      setUrl(objectUrl);
      return () => URL.revokeObjectURL(objectUrl);
    }

    // No local copy yet and the blob isn't flushed → don't fetch (guaranteed
    // 404); the pending hint renders below. The COMPLETE re-pull flips
    // pendingFlush false + refreshKey, re-running this effect to hit /raw.
    if (pendingFlush) {
      return;
    }

    if (!sessionId) {
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    fetchArtifactRawObjectUrl(sessionId, artifactId)
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
  }, [localFile, pendingFlush, sessionId, artifactId, refreshKey]);

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
        {pendingFlush ? '图片生成中，完成后显示…' : '加载图片中...'}
      </div>
    );
  }
  return (
    <div className="h-full overflow-auto flex items-start justify-center p-4">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={originalFilename || artifactId} className="max-w-full h-auto" />
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { fetchArtifactRawObjectUrl } from '@/lib/api';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStagedFilesStore } from '@/stores/stagedFilesStore';

/** Render an image artifact (content_type image/*). Source depends on whether the
 *  artifact is live THIS turn (pendingFlush = liveContent[id], cleared at COMPLETE):
 *
 *  - Live this turn:
 *      · user upload → the staged File still in the composer (instant, no fetch);
 *        matched by name, which is unique per turn (composer dedups, backend echoes
 *        it as original_filename), so it can't bind to the wrong upload.
 *      · tool/model-generated (no local copy) → "being saved" hint, NOT an error
 *        (blob isn't flushed yet → /raw would 404). COMPLETE re-runs us → /raw.
 *  - Settled (past-turn, or post-COMPLETE) → authed /raw fetch (an <img src> can't
 *    carry the JWT) → object URL. Never uses a staged File — a same-named file
 *    staged for a later turn must not shadow this artifact's own DB blob.
 *
 *  The user never sees a raw backend error mid-turn; a real failure shows a clean
 *  generic message — the detailed error + request id stay in the server log. */
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
    originalFilename
      ? s.drafts[s.activeKey]?.files.find((f) => f.file.name === originalFilename)?.file
      : undefined
  );
  // Live this turn, not yet flushed (created/updated this turn). Cleared at COMPLETE.
  const pendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);

  useEffect(() => {
    setUrl(null);
    setError(null);

    // Live this turn (created/updated, blob not yet flushed). The staged-File
    // fallback is scoped to THIS branch deliberately: a settled / past-turn
    // artifact must read its OWN DB blob, never a same-named File staged for a
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

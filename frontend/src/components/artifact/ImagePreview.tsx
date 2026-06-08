'use client';

import { useEffect, useState } from 'react';
import { fetchArtifactRawObjectUrl } from '@/lib/api';

/** Render an image artifact (content_type image/*). Fetches the raw blob with auth
 *  (an <img src> can't carry the JWT) → object URL, revoked on unmount / id change.
 *  The blob is DB-only server-side, so an image uploaded *this* turn shows only after
 *  the turn completes (the COMPLETE re-pull) — same REST-lags-live tradeoff as all
 *  artifacts; until then the fetch 404s and we show the error state.
 *
 *  refreshKey re-runs the fetch when the artifact's identity is unchanged but its
 *  backing data may now exist: a mid-turn upload 404s, then the COMPLETE re-pull
 *  swaps the live detail (updated_at '' → real timestamp) for the DB one — without
 *  this dep the effect wouldn't re-fire and the user would stay stuck on the error. */
export default function ImagePreview({
  sessionId,
  artifactId,
  refreshKey,
}: {
  sessionId: string;
  artifactId: string;
  refreshKey?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setUrl(null);
    setError(null);
    if (!sessionId) {
      return;
    }
    fetchArtifactRawObjectUrl(sessionId, artifactId)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message ?? '加载图片失败');
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sessionId, artifactId, refreshKey]);

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
        加载图片中...
      </div>
    );
  }
  return (
    <div className="h-full overflow-auto flex items-start justify-center p-4">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={artifactId} className="max-w-full h-auto" />
    </div>
  );
}

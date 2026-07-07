'use client';

import { useEffect, useState } from 'react';
import { fetchArtifactRawObjectUrl } from '@/lib/api';
import { useArtifactStore } from '@/stores/artifactStore';
import BinaryFilePreview from './BinaryFilePreview';

export default function PdfPreview({
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
  const pendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (pendingFlush) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    setUrl(null);
    setError(null);

    fetchArtifactRawObjectUrl(sessionId, artifactId)
      .then((nextUrl) => {
        objectUrl = nextUrl;
        if (!cancelled) setUrl(nextUrl);
      })
      .catch(() => {
        if (!cancelled) setError('PDF 预览失败，可下载原件查看');
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifactId, pendingFlush, sessionId]);

  if (pendingFlush || error) {
    return (
      <BinaryFilePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        description={error ?? 'PDF 原件将在本回合完成后可预览'}
        pendingMessage="本回合完成后可预览或下载原件"
      />
    );
  }

  if (!url) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
        正在加载 PDF...
      </div>
    );
  }

  return (
    <iframe
      title="PDF preview"
      src={url}
      referrerPolicy="no-referrer"
      className="block h-full w-full border-0 bg-white"
    />
  );
}

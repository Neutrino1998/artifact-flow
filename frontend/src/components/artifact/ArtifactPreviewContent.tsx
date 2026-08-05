'use client';

import type { ArtifactRawBlobFetcher, ArtifactRawObjectUrlFetcher } from '@/lib/api';
import { isCsvMime, isDocxMime, isPdfMime, isSpreadsheetMime } from '@/lib/artifactPreview';
import { isSafeInlineImageMime } from '@/lib/mime';
import BinaryFilePreview from './BinaryFilePreview';
import CsvPreview from './CsvPreview';
import DocxPreview from './DocxPreview';
import HtmlPreview from './HtmlPreview';
import ImagePreview from './ImagePreview';
import MarkdownPreview from './MarkdownPreview';
import PdfPreview from './PdfPreview';
import SpreadsheetPreview from './SpreadsheetPreview';

export default function ArtifactPreviewContent({
  sessionId,
  artifactId,
  content,
  contentType,
  hasBlob,
  originalFilename,
  refreshKey,
  fetchRawBlob,
  fetchRawObjectUrl,
  pendingFlush,
  useLocalPreview,
  showUnsupportedBinaryDownload,
}: {
  sessionId: string;
  artifactId: string;
  content: string;
  contentType: string;
  hasBlob?: boolean;
  originalFilename?: string | null;
  refreshKey?: string;
  fetchRawBlob?: ArtifactRawBlobFetcher;
  fetchRawObjectUrl?: ArtifactRawObjectUrlFetcher;
  pendingFlush?: boolean;
  useLocalPreview?: boolean;
  showUnsupportedBinaryDownload?: boolean;
}) {
  const isImage = isSafeInlineImageMime(contentType);
  const isBinary = !!hasBlob && !isImage;
  const isHtml = contentType === 'text/html';

  if (isImage) {
    return (
      <ImagePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        refreshKey={refreshKey}
        fetchRawObjectUrl={fetchRawObjectUrl}
        pendingFlush={pendingFlush}
        useLocalPreview={useLocalPreview}
      />
    );
  }

  if (isCsvMime(contentType)) {
    return (
      <CsvPreview
        content={content}
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        hasBlob={!!hasBlob}
        fetchRawBlob={fetchRawBlob}
        fetchRawObjectUrl={fetchRawObjectUrl}
        pendingFlush={pendingFlush}
      />
    );
  }

  if (isBinary && isPdfMime(contentType)) {
    return (
      <PdfPreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        fetchRawObjectUrl={fetchRawObjectUrl}
        pendingFlush={pendingFlush}
      />
    );
  }

  if (isBinary && isDocxMime(contentType)) {
    return (
      <DocxPreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        fetchRawBlob={fetchRawBlob}
        fetchRawObjectUrl={fetchRawObjectUrl}
        pendingFlush={pendingFlush}
      />
    );
  }

  if (isBinary && isSpreadsheetMime(contentType)) {
    return (
      <SpreadsheetPreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        fetchRawBlob={fetchRawBlob}
        fetchRawObjectUrl={fetchRawObjectUrl}
        pendingFlush={pendingFlush}
      />
    );
  }

  if (isBinary) {
    return (
      <BinaryFilePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        description={`${contentType} · 二进制文件暂不能预览，可下载原件查看`}
        showDownload={showUnsupportedBinaryDownload}
        fetchRawObjectUrl={fetchRawObjectUrl}
        pendingFlush={pendingFlush}
      />
    );
  }

  if (isHtml) return <HtmlPreview content={content} />;
  return <MarkdownPreview content={content} />;
}

'use client';

import { useEffect, useMemo, useState } from 'react';
import { fetchArtifactRawBlob } from '@/lib/api';
import { parseCsvPreview } from '@/lib/csvPreview';
import { useArtifactStore } from '@/stores/artifactStore';
import BinaryFilePreview from './BinaryFilePreview';

const MAX_ROWS = 200;
const MAX_COLUMNS = 50;
const MAX_BLOB_PREVIEW_BYTES = 2 * 1024 * 1024;
const MAX_CELL_CHARS = 240;

function displayCell(value: unknown): string {
  const text = String(value ?? '');
  return text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS)}...` : text;
}

export default function CsvPreview({
  content,
  sessionId,
  artifactId,
  originalFilename,
  contentType,
  hasBlob,
}: {
  content: string;
  sessionId: string;
  artifactId: string;
  originalFilename?: string | null;
  contentType: string;
  hasBlob: boolean;
}) {
  const pendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);
  const [blobContent, setBlobContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hasBlob || pendingFlush) return;
    let cancelled = false;
    setBlobContent(null);
    setError(null);

    fetchArtifactRawBlob(sessionId, artifactId)
      .then(async (blob) => {
        if (blob.size > MAX_BLOB_PREVIEW_BYTES) {
          throw new Error('CSV 文件较大，已跳过浏览器内预览');
        }
        const text = await blob.text();
        if (!cancelled) setBlobContent(text);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'CSV 预览失败');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [artifactId, hasBlob, pendingFlush, sessionId]);

  const previewContent = hasBlob ? blobContent ?? '' : content;
  const parsed = useMemo(
    () => parseCsvPreview(previewContent, { maxRows: MAX_ROWS, maxColumns: MAX_COLUMNS }),
    [previewContent]
  );

  if (hasBlob && (pendingFlush || error)) {
    return (
      <BinaryFilePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        description={error ?? 'CSV 原件将在本回合完成后可预览'}
        pendingMessage="本回合完成后可预览或下载原件"
      />
    );
  }

  if (hasBlob && blobContent === null) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
        正在解析 CSV...
      </div>
    );
  }

  const columnCount = Math.max(1, ...parsed.rows.map((row) => row.length));
  const header = parsed.rows[0] ?? [];
  const bodyRows = parsed.rows.slice(1);

  return (
    <div className="h-full min-h-0 flex flex-col bg-white dark:bg-surface-dark">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-border dark:border-border-dark text-xs text-text-tertiary dark:text-text-tertiary-dark">
        <span>{parsed.rows.length} 行</span>
        <span>{Math.min(parsed.maxColumnsSeen || columnCount, MAX_COLUMNS)} 列</span>
        {(parsed.truncatedRows || parsed.truncatedColumns) && (
          <span>已截断为前 {MAX_ROWS} 行 / {MAX_COLUMNS} 列</span>
        )}
      </div>
      <div className="flex-1 overflow-auto">
        <table className="min-w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-bg dark:bg-bg-dark">
            <tr>
              <th className="sticky left-0 z-20 w-12 border-b border-r border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-2 py-1 text-right font-mono text-text-tertiary dark:text-text-tertiary-dark">
                #
              </th>
              {Array.from({ length: columnCount }).map((_, idx) => (
                <th
                  key={idx}
                  className="max-w-72 border-b border-r border-border dark:border-border-dark px-3 py-1.5 text-left font-semibold text-text-primary dark:text-text-primary-dark"
                  title={header[idx] ?? ''}
                >
                  <span className="block truncate">{displayCell(header[idx] ?? `列 ${idx + 1}`)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bodyRows.map((row, rowIdx) => (
              <tr key={rowIdx} className="hover:bg-bg dark:hover:bg-bg-dark">
                <td className="sticky left-0 w-12 border-b border-r border-border dark:border-border-dark bg-white dark:bg-surface-dark px-2 py-1 text-right font-mono text-text-tertiary dark:text-text-tertiary-dark">
                  {rowIdx + 2}
                </td>
                {Array.from({ length: columnCount }).map((_, colIdx) => (
                  <td
                    key={colIdx}
                    className="max-w-72 border-b border-r border-border dark:border-border-dark px-3 py-1.5 align-top text-text-primary dark:text-text-primary-dark"
                    title={row[colIdx] ?? ''}
                  >
                    <span className="block truncate">{displayCell(row[colIdx]) || '\u00A0'}</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { fetchArtifactRawBlob } from '@/lib/api';
import { useArtifactStore } from '@/stores/artifactStore';
import BinaryFilePreview from './BinaryFilePreview';

const MAX_PREVIEW_BYTES = 8 * 1024 * 1024;
const MAX_ROWS = 200;
const MAX_COLUMNS = 50;
const MAX_SHEETS = 12;
const MAX_CELL_CHARS = 240;

interface SheetPreview {
  name: string;
  rows: string[][];
  totalRows: number;
  totalColumns: number;
  truncatedRows: boolean;
  truncatedColumns: boolean;
}

function displayCell(value: unknown): string {
  const text = String(value ?? '');
  return text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS)}...` : text;
}

function cellToString(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (value instanceof Date) return value.toLocaleString();
  return String(value);
}

export default function SpreadsheetPreview({
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
  const [sheets, setSheets] = useState<SheetPreview[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (pendingFlush) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSheets([]);
    setActiveSheet(0);

    fetchArtifactRawBlob(sessionId, artifactId)
      .then(async (blob) => {
        if (blob.size > MAX_PREVIEW_BYTES) {
          throw new Error('表格文件较大，已跳过浏览器内预览');
        }

        const { default: readXlsxFile } = await import('read-excel-file/browser');
        const workbook = await readXlsxFile(blob);
        const nextSheets: SheetPreview[] = workbook.slice(0, MAX_SHEETS).map(({ sheet, data }) => {
          const totalRows = data.length;
          const totalColumns = data.reduce((max, row) => Math.max(max, row.length), 0);
          return {
            name: sheet,
            rows: data
              .slice(0, MAX_ROWS)
              .map((row) => row.slice(0, MAX_COLUMNS).map(cellToString)),
            totalRows,
            totalColumns,
            truncatedRows: totalRows > MAX_ROWS,
            truncatedColumns: totalColumns > MAX_COLUMNS,
          };
        });

        if (!cancelled) setSheets(nextSheets);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '表格预览失败，可下载原件查看');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [artifactId, pendingFlush, sessionId]);

  if (pendingFlush || error) {
    return (
      <BinaryFilePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        description={error ?? '表格原件将在本回合完成后可预览'}
        pendingMessage="本回合完成后可预览或下载原件"
      />
    );
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
        正在解析表格...
      </div>
    );
  }

  const sheet = sheets[Math.min(activeSheet, Math.max(0, sheets.length - 1))];
  if (!sheet) {
    return (
      <BinaryFilePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        description="没有可预览的工作表，可下载原件查看"
      />
    );
  }

  const columnCount = Math.max(1, ...sheet.rows.map((row) => row.length));
  const header = sheet.rows[0] ?? [];
  const bodyRows = sheet.rows.slice(1);

  return (
    <div className="h-full min-h-0 flex flex-col bg-white dark:bg-surface-dark">
      <div
        role="tablist"
        aria-label="工作表"
        className="flex min-h-0 items-end gap-1 overflow-x-auto border-b border-border bg-bg px-3 pt-2 dark:border-border-dark dark:bg-bg-dark"
      >
        {sheets.map((item, idx) => (
          <button
            key={`${item.name}-${idx}`}
            type="button"
            role="tab"
            aria-selected={idx === activeSheet}
            onClick={() => setActiveSheet(idx)}
            className={`-mb-px shrink-0 rounded-t-md border px-3 py-1.5 text-xs font-medium transition-colors ${
              idx === activeSheet
                ? 'border-border border-b-white bg-white text-text-primary shadow-sm dark:border-border-dark dark:border-b-surface-dark dark:bg-surface-dark dark:text-text-primary-dark'
                : 'border-transparent bg-panel-accent text-text-secondary hover:border-border hover:bg-white hover:text-text-primary dark:bg-panel-accent-dark dark:text-text-secondary-dark dark:hover:border-border-dark dark:hover:bg-surface-dark dark:hover:text-text-primary-dark'
            }`}
          >
            {item.name}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3 px-4 py-2 border-b border-border dark:border-border-dark text-xs text-text-tertiary dark:text-text-tertiary-dark">
        <span>{sheet.totalRows} 行</span>
        <span>{sheet.totalColumns} 列</span>
        {(sheet.truncatedRows || sheet.truncatedColumns || sheets.length === MAX_SHEETS) && (
          <span>预览前 {MAX_ROWS} 行 / {MAX_COLUMNS} 列 / {MAX_SHEETS} 个工作表</span>
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

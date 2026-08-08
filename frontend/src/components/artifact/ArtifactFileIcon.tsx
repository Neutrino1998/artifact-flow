import {
  IconFile,
  IconFileCode,
  IconFileText,
  IconFileTypeCsv,
  IconFileTypeHtml,
  IconFileTypeJpg,
  IconFileTypePdf,
  IconFileTypePng,
  IconFileTypePpt,
  IconFileTypeSvg,
  IconFileTypeTxt,
  IconFileTypeXls,
  IconFileTypeZip,
  IconFileWord,
  IconFolder,
  IconPhoto,
} from '@tabler/icons-react';
import { Folders } from 'lucide-react';

const MIME_LABELS: Record<string, string> = {
  'text/markdown': 'MD',
  'text/html': 'HTML',
  'text/csv': 'CSV',
  'application/csv': 'CSV',
  'application/json': 'JSON',
  'application/pdf': 'PDF',
  'application/msword': 'DOC',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'application/vnd.ms-word.document.macroenabled.12': 'DOCM',
  'application/vnd.ms-excel': 'XLS',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
  'application/vnd.ms-excel.sheet.macroenabled.12': 'XLSM',
  'application/vnd.ms-powerpoint': 'PPT',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
  'application/vnd.ms-powerpoint.presentation.macroenabled.12': 'PPTM',
  'application/zip': 'ZIP',
};

function normalizedMime(contentType: string): string {
  return contentType.split(';', 1)[0].trim().toLowerCase();
}

function filenameExtension(filename?: string | null): string | null {
  const match = filename?.match(/\.([a-z0-9]{1,5})$/i);
  return match?.[1].toUpperCase() ?? null;
}

export function artifactFileTypeLabel(
  contentType: string,
  filename?: string | null,
): string {
  const mime = normalizedMime(contentType);
  const known = MIME_LABELS[mime];
  if (known) return known;
  if (mime.endsWith('+json')) return 'JSON';

  const extension = filenameExtension(filename);
  if (extension) return extension;

  if (mime.startsWith('image/')) {
    const subtype = mime.slice('image/'.length).split('+', 1)[0];
    return subtype === 'jpeg' ? 'JPG' : subtype.slice(0, 5).toUpperCase();
  }
  if (mime === 'text/plain') return 'TXT';
  if (mime.startsWith('text/')) {
    return mime.slice('text/'.length).replace(/^x-/, '').slice(0, 5).toUpperCase();
  }
  return mime === 'application/octet-stream' ? 'BIN' : 'FILE';
}

function fileColorClass(contentType: string, label: string): string {
  const mime = normalizedMime(contentType);
  if (['CSV', 'XLS', 'XLSX', 'XLSM'].includes(label)) return 'text-status-success';
  if (['HTML', 'PPT', 'PPTX', 'PPTM'].includes(label)) return 'text-status-warning';
  if (mime.startsWith('image/')) return 'text-trace-llm dark:text-trace-llm-dark';
  if (label === 'PDF') return 'text-status-error';
  if (['MD', 'DOC', 'DOCX', 'DOCM'].includes(label)) {
    return 'text-trace-tool dark:text-trace-tool-dark';
  }
  return 'text-text-tertiary dark:text-text-tertiary-dark';
}

function SemanticFileIcon({ label, contentType }: { label: string; contentType: string }) {
  const mime = normalizedMime(contentType);
  const props = { className: 'h-full w-full', stroke: 1.65 };

  if (['DOC', 'DOCX', 'DOCM'].includes(label)) return <IconFileWord {...props} />;
  if (label === 'CSV') return <IconFileTypeCsv {...props} />;
  if (['XLS', 'XLSX', 'XLSM'].includes(label)) return <IconFileTypeXls {...props} />;
  if (label === 'PDF') return <IconFileTypePdf {...props} />;
  if (label === 'PNG') return <IconFileTypePng {...props} />;
  if (label === 'JPG' || label === 'JPEG') return <IconFileTypeJpg {...props} />;
  if (label === 'SVG') return <IconFileTypeSvg {...props} />;
  if (label === 'HTML') return <IconFileTypeHtml {...props} />;
  if (label === 'TXT') return <IconFileTypeTxt {...props} />;
  if (label === 'ZIP') return <IconFileTypeZip {...props} />;
  if (['PPT', 'PPTX', 'PPTM'].includes(label)) return <IconFileTypePpt {...props} />;
  if (mime === 'application/json' || mime.endsWith('+json')) return <IconFileCode {...props} />;
  if (mime.startsWith('image/')) return <IconPhoto {...props} />;
  if (mime.startsWith('text/')) return <IconFileText {...props} />;
  return <IconFile {...props} />;
}

export function ArtifactFileIcon({
  contentType,
  filename,
  compact = false,
}: {
  contentType: string;
  filename?: string | null;
  compact?: boolean;
}) {
  const label = artifactFileTypeLabel(contentType, filename);
  return (
    <span
      aria-hidden="true"
      className={`${compact ? 'h-4 w-4' : 'h-5 w-5'} shrink-0 ${fileColorClass(
        contentType,
        label,
      )}`}
    >
      <SemanticFileIcon label={label} contentType={contentType} />
    </span>
  );
}

export function ArtifactFolderIcon() {
  return (
    <IconFolder
      aria-hidden="true"
      className="h-5 w-5 shrink-0 text-accent"
      stroke={1.65}
    />
  );
}

export function ArtifactBrowserIcon({ size = 14 }: { size?: number }) {
  return (
    <Folders
      aria-hidden="true"
      absoluteStrokeWidth
      className="shrink-0"
      size={size}
      strokeWidth={1.5}
    />
  );
}

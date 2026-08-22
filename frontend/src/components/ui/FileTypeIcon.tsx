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
  IconPhoto,
} from '@tabler/icons-react';

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

const IMAGE_LABELS = new Set([
  'AVIF',
  'BMP',
  'GIF',
  'HEIC',
  'HEIF',
  'JPG',
  'JPEG',
  'PNG',
  'TIFF',
  'WEBP',
]);

const CODE_LABELS = new Set(['CSS', 'JS', 'JSX', 'JSON', 'PY', 'TS', 'TSX']);

function normalizedMime(contentType?: string | null): string {
  return (contentType ?? '').split(';', 1)[0].trim().toLowerCase();
}

function filenameExtension(filename?: string | null): string | null {
  const match = filename?.match(/\.([a-z0-9]{1,5})$/i);
  return match?.[1].toUpperCase() ?? null;
}

export function fileTypeLabel(
  contentType?: string | null,
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

function fileColorClass(contentType: string | null | undefined, label: string): string {
  const mime = normalizedMime(contentType);
  if (['CSV', 'XLS', 'XLSX', 'XLSM'].includes(label)) return 'text-status-success';
  if (['HTML', 'PPT', 'PPTX', 'PPTM'].includes(label)) return 'text-status-warning';
  if (mime.startsWith('image/') || IMAGE_LABELS.has(label)) {
    return 'text-trace-llm dark:text-trace-llm-dark';
  }
  if (label === 'PDF') return 'text-status-error';
  if (['MD', 'DOC', 'DOCX', 'DOCM'].includes(label)) {
    return 'text-trace-tool dark:text-trace-tool-dark';
  }
  return 'text-text-tertiary dark:text-text-tertiary-dark';
}

function SemanticFileIcon({
  label,
  contentType,
}: {
  label: string;
  contentType?: string | null;
}) {
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
  if (CODE_LABELS.has(label) || mime === 'application/json' || mime.endsWith('+json')) {
    return <IconFileCode {...props} />;
  }
  if (mime.startsWith('image/') || IMAGE_LABELS.has(label)) return <IconPhoto {...props} />;
  if (mime.startsWith('text/') || label === 'MD') return <IconFileText {...props} />;
  return <IconFile {...props} />;
}

export function FileTypeIcon({
  contentType,
  filename,
  size = 16,
}: {
  contentType?: string | null;
  filename?: string | null;
  size?: number;
}) {
  const label = fileTypeLabel(contentType, filename);
  return (
    <span
      aria-hidden="true"
      className={`shrink-0 ${fileColorClass(contentType, label)}`}
      style={{ height: size, width: size }}
    >
      <SemanticFileIcon label={label} contentType={contentType} />
    </span>
  );
}

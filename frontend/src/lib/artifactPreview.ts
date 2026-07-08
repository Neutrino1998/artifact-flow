const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function baseMime(contentType: string | null | undefined): string {
  return (contentType ?? '').split(';', 1)[0].trim().toLowerCase();
}

export function isPdfMime(contentType: string | null | undefined): boolean {
  return baseMime(contentType) === 'application/pdf';
}

export function isDocxMime(contentType: string | null | undefined): boolean {
  return baseMime(contentType) === DOCX_MIME;
}

export function isCsvMime(contentType: string | null | undefined): boolean {
  return baseMime(contentType) === 'text/csv';
}

export function isSpreadsheetMime(contentType: string | null | undefined): boolean {
  const mime = baseMime(contentType);
  return (
    mime === XLSX_MIME ||
    mime === 'application/vnd.ms-excel.sheet.macroenabled.12'
  );
}

export function hasRichArtifactPreview(
  contentType: string | null | undefined,
  hasBlob: boolean | undefined
): boolean {
  if (isCsvMime(contentType)) return true;
  if (!hasBlob) return false;
  return isPdfMime(contentType) || isDocxMime(contentType) || isSpreadsheetMime(contentType);
}

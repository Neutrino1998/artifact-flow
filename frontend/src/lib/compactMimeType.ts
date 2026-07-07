const MIME_LABELS: Record<string, string> = {
  'application/msword': 'DOC',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'application/vnd.ms-word.document.macroenabled.12': 'DOCM',
  'application/vnd.ms-excel': 'XLS',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
  'application/vnd.ms-excel.sheet.macroenabled.12': 'XLSM',
  'application/vnd.ms-powerpoint': 'PPT',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
  'application/vnd.ms-powerpoint.presentation.macroenabled.12': 'PPTM',
};

export function compactMimeType(mimeType: string, maxLength = 24): string {
  const value = mimeType.trim();
  const label = MIME_LABELS[value.toLowerCase()];
  if (label) return label;

  if (value.length <= maxLength) return value;
  if (maxLength <= 3) return value.slice(0, maxLength);

  const available = maxLength - 3;
  const headLength = Math.ceil(available * 0.6);
  const tailLength = available - headLength;

  return `${value.slice(0, headLength)}...${value.slice(-tailLength)}`;
}

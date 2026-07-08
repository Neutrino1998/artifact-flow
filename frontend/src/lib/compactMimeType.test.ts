import { describe, expect, test } from 'vitest';
import { compactMimeType } from './compactMimeType';

describe('compactMimeType', () => {
  test('keeps short MIME types unchanged', () => {
    expect(compactMimeType('text/markdown')).toBe('text/markdown');
  });

  test('uses short labels for verbose Office MIME types', () => {
    expect(
      compactMimeType('application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    ).toBe('DOCX');
    expect(
      compactMimeType('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    ).toBe('XLSX');
  });

  test('truncates other long MIME types while keeping both ends visible', () => {
    const archiveMime = 'application/vnd.some-very-long-custom-package+zip';

    expect(compactMimeType(archiveMime)).toBe('application/v...kage+zip');
  });

  test('trims surrounding whitespace before measuring', () => {
    expect(compactMimeType('  application/pdf  ')).toBe('application/pdf');
  });
});

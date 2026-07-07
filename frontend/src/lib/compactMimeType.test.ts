import { describe, expect, test } from 'vitest';
import { compactMimeType } from './compactMimeType';

describe('compactMimeType', () => {
  test('keeps short MIME types unchanged', () => {
    expect(compactMimeType('text/markdown')).toBe('text/markdown');
  });

  test('truncates long MIME types while keeping both ends visible', () => {
    const docxMime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

    expect(compactMimeType(docxMime)).toBe('application/vnd.open...ngml.document');
  });

  test('trims surrounding whitespace before measuring', () => {
    expect(compactMimeType('  application/pdf  ')).toBe('application/pdf');
  });
});

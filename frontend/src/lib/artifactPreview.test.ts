import { describe, expect, it } from 'vitest';
import {
  hasRichArtifactPreview,
  isCsvMime,
  isDocxMime,
  isPdfMime,
  isSpreadsheetMime,
} from './artifactPreview';

describe('artifact preview MIME helpers', () => {
  it('recognizes rich preview MIME types with optional parameters', () => {
    expect(isPdfMime('application/pdf')).toBe(true);
    expect(isDocxMime('application/vnd.openxmlformats-officedocument.wordprocessingml.document')).toBe(true);
    expect(isSpreadsheetMime('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')).toBe(true);
    expect(isCsvMime('text/csv; charset=utf-8')).toBe(true);
  });

  it('requires blobs for binary rich previews but allows text CSV', () => {
    expect(hasRichArtifactPreview('application/pdf', true)).toBe(true);
    expect(hasRichArtifactPreview('application/pdf', false)).toBe(false);
    expect(hasRichArtifactPreview('text/csv', false)).toBe(true);
  });
});

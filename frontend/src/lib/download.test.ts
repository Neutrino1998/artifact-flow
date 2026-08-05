import { describe, expect, test } from 'vitest';

import { getTextArtifactDownloadFilename } from './download';

describe('getTextArtifactDownloadFilename', () => {
  test('uses the extension associated with the artifact MIME type', () => {
    expect(getTextArtifactDownloadFilename('Run report', 'text/markdown')).toBe('Run report.md');
    expect(getTextArtifactDownloadFilename('Payload', 'application/json')).toBe('Payload.json');
  });

  test('sanitizes filename separators and falls back to text', () => {
    expect(getTextArtifactDownloadFilename('draft/report:1', 'application/x-custom')).toBe(
      'draft-report-1.txt'
    );
  });
});

import { describe, expect, it } from 'vitest';
import { parseCsvPreview } from './csvPreview';

describe('parseCsvPreview', () => {
  it('handles quoted commas, escaped quotes, and CRLF rows', () => {
    const parsed = parseCsvPreview('name,note\r\nAlice,"a,b"\r\nBob,"said ""hi"""', {
      maxRows: 10,
      maxColumns: 10,
    });

    expect(parsed.rows).toEqual([
      ['name', 'note'],
      ['Alice', 'a,b'],
      ['Bob', 'said "hi"'],
    ]);
    expect(parsed.truncatedRows).toBe(false);
    expect(parsed.truncatedColumns).toBe(false);
  });

  it('caps rows and columns', () => {
    const parsed = parseCsvPreview('a,b,c\n1,2,3\n4,5,6', {
      maxRows: 2,
      maxColumns: 2,
    });

    expect(parsed.rows).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ]);
    expect(parsed.truncatedRows).toBe(true);
    expect(parsed.truncatedColumns).toBe(true);
    expect(parsed.maxColumnsSeen).toBe(3);
  });
});

import { describe, expect, test } from 'vitest';
import { formatCachedTokens, formatTokenUsage } from './formatTokens';

describe('formatCachedTokens', () => {
  test('marks a partial aggregate as a lower bound', () => {
    expect(formatCachedTokens(8_200, true)).toBe('≥8.2K ↻');
  });

  test('leaves a complete aggregate exact', () => {
    expect(formatCachedTokens(8_200)).toBe('8.2K ↻');
  });
});

describe('formatTokenUsage', () => {
  test('shows an explicitly reported zero cache hit', () => {
    expect(formatTokenUsage({
      input_tokens: 900,
      cached_input_tokens: 0,
      output_tokens: 20,
    })).toBe('900 ↑ (0 ↻) · 20 ↓');
  });

  test('omits cache usage when the provider did not report it', () => {
    expect(formatTokenUsage({
      input_tokens: 900,
      output_tokens: 20,
    })).toBe('900 ↑ · 20 ↓');
  });
});

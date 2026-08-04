import { describe, expect, test } from 'vitest';
import { formatTokenUsage } from './formatTokens';

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

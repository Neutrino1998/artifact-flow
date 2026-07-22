import { describe, expect, test } from 'vitest';
import { formatDuration } from './formatDuration';

describe('formatDuration', () => {
  test('keeps sub-second durations in milliseconds', () => {
    expect(formatDuration(999)).toBe('999ms');
  });

  test('shows tenths of a second below one minute', () => {
    expect(formatDuration(8_742)).toBe('8.7s');
  });

  test('shows minutes and remaining whole seconds', () => {
    expect(formatDuration(125_900)).toBe('2m 5s');
  });
});

import { describe, expect, test } from 'vitest';
import type { VersionSummary } from '@/types';
import { findPrevVersion } from './artifactVersions';

function version(version: number): VersionSummary {
  return { version, update_type: 'update', created_at: '' };
}

describe('findPrevVersion', () => {
  test('uses persisted ordering rather than arithmetic for sparse versions', () => {
    expect(findPrevVersion([version(1), version(3), version(7)], 7)).toBe(3);
  });

  test('returns null for the first persisted version', () => {
    expect(findPrevVersion([version(4)], 4)).toBeNull();
  });
});

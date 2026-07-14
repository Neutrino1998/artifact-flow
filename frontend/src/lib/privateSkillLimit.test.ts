import { describe, expect, it } from 'vitest';

import { resolvePrivateSkillAllowance } from './privateSkillLimit';

describe('resolvePrivateSkillAllowance', () => {
  it('allows imports while meta is unavailable', () => {
    expect(resolvePrivateSkillAllowance(2, null)).toEqual({
      kind: 'unknown', used: 2, canImport: true,
    });
  });

  it('treats -1 as unlimited', () => {
    expect(resolvePrivateSkillAllowance(20, -1)).toEqual({
      kind: 'unlimited', used: 20, canImport: true,
    });
  });

  it('treats 0 as disabled', () => {
    expect(resolvePrivateSkillAllowance(0, 0)).toEqual({
      kind: 'disabled', used: 0, canImport: false,
    });
  });

  it('reports remaining slots below a positive limit', () => {
    expect(resolvePrivateSkillAllowance(2, 3)).toEqual({
      kind: 'limited', used: 2, limit: 3, remaining: 1, canImport: true,
    });
  });

  it('clamps remaining slots and blocks at or above the limit', () => {
    expect(resolvePrivateSkillAllowance(4, 3)).toEqual({
      kind: 'limited', used: 4, limit: 3, remaining: 0, canImport: false,
    });
  });
});

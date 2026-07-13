export type PrivateSkillAllowance =
  | { kind: 'unknown'; used: number; canImport: true }
  | { kind: 'unlimited'; used: number; canImport: true }
  | { kind: 'disabled'; used: number; canImport: false }
  | {
      kind: 'limited';
      used: number;
      limit: number;
      remaining: number;
      canImport: boolean;
    };

export function resolvePrivateSkillAllowance(
  used: number,
  limit: number | null,
): PrivateSkillAllowance {
  if (limit === null) return { kind: 'unknown', used, canImport: true };
  if (limit < 0) return { kind: 'unlimited', used, canImport: true };
  if (limit === 0) return { kind: 'disabled', used, canImport: false };

  const remaining = Math.max(limit - used, 0);
  return {
    kind: 'limited',
    used,
    limit,
    remaining,
    canImport: remaining > 0,
  };
}

/** Backend-owned subset of the password policy used for immediate UX guidance. */
export interface PasswordPolicy {
  minLength: number;
  maxBytes: number;
  requireLetter: boolean;
  requireDigit: boolean;
  requireSymbol: boolean;
}

const GENERIC_PASSWORD_POLICY_HINT = '密码须满足服务端安全策略';

export function getPasswordPolicyHint(policy: PasswordPolicy | null): string {
  if (!policy) return GENERIC_PASSWORD_POLICY_HINT;

  const required: string[] = [];
  if (policy.requireLetter) required.push('字母');
  if (policy.requireDigit) required.push('数字');
  if (policy.requireSymbol) required.push('符号');

  const lengthHint = `至少 ${policy.minLength} 位，UTF-8 编码后最多 ${policy.maxBytes} 字节`;
  return required.length > 0
    ? `${lengthHint}，须同时包含${required.join('、')}`
    : lengthHint;
}

/** 返回不达标原因(中文,可直接展示);达标返回 null。 */
export function validatePasswordStrength(
  pw: string,
  policy: PasswordPolicy | null,
): string | null {
  // Meta is best-effort UX. Never reject locally when the backend policy is
  // unavailable; submission still reaches the authoritative backend validator.
  if (!policy) return null;

  if (new TextEncoder().encode(pw).byteLength > policy.maxBytes) {
    return `口令 UTF-8 编码后不能超过 ${policy.maxBytes} 字节`;
  }
  // Array.from counts Unicode code points, matching Python len(str) on the
  // backend; String.length would count an emoji as two UTF-16 code units.
  if (Array.from(pw).length < policy.minLength) {
    return `口令长度不足，至少需要 ${policy.minLength} 位`;
  }
  const missing: string[] = [];
  if (policy.requireLetter && !/[A-Za-z]/.test(pw)) missing.push('字母');
  if (policy.requireDigit && !/[0-9]/.test(pw)) missing.push('数字');
  if (policy.requireSymbol && !/[^A-Za-z0-9]/.test(pw)) missing.push('符号');
  if (missing.length > 0) {
    return `口令复杂度不足，必须同时包含${missing.join('、')}`;
  }
  return null;
}

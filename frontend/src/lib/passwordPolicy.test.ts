import { describe, test, expect } from 'vitest';
import {
  getPasswordPolicyHint,
  type PasswordPolicy,
  validatePasswordStrength,
} from './passwordPolicy';

const defaultPolicy: PasswordPolicy = {
  minLength: 8,
  maxBytes: 72,
  requireLetter: true,
  requireDigit: true,
  requireSymbol: true,
};

describe('validatePasswordStrength', () => {
  test('accepts a strong password', () => {
    expect(validatePasswordStrength('Abcd123!', defaultPolicy)).toBeNull();
  });

  test('rejects too short', () => {
    const msg = validatePasswordStrength('Ab1!', defaultPolicy);
    expect(msg).toMatch(/长度/);
    expect('Ab1!'.length).toBeLessThan(defaultPolicy.minLength);
  });

  test('counts Unicode code points like the backend', () => {
    const policy = { ...defaultPolicy, minLength: 5 };

    expect(validatePasswordStrength('😀A1!', policy)).toMatch(/至少需要 5 位/);
  });

  test('rejects a password over the bcrypt UTF-8 byte limit', () => {
    const password = 'Aa1!' + '中'.repeat(23); // 73 UTF-8 bytes

    expect(validatePasswordStrength(password, defaultPolicy)).toMatch(/72 字节/);
  });

  test('accepts a password at the bcrypt UTF-8 byte limit', () => {
    const password = 'Aa1!' + '中'.repeat(22) + 'xy'; // 72 UTF-8 bytes

    expect(validatePasswordStrength(password, defaultPolicy)).toBeNull();
  });

  test('rejects missing symbol', () => {
    expect(validatePasswordStrength('Abcd1234', defaultPolicy)).toMatch(/符号/);
  });

  test('rejects missing digit', () => {
    expect(validatePasswordStrength('Abcdefg!', defaultPolicy)).toMatch(/数字/);
  });

  test('rejects missing letter', () => {
    expect(validatePasswordStrength('1234567!', defaultPolicy)).toMatch(/字母/);
  });

  test('follows a relaxed backend policy', () => {
    const relaxed = { ...defaultPolicy, minLength: 6, requireSymbol: false };

    expect(validatePasswordStrength('Ab1234', relaxed)).toBeNull();
    expect(getPasswordPolicyHint(relaxed)).toBe(
      '至少 6 位，UTF-8 编码后最多 72 字节，须同时包含字母、数字',
    );
  });

  test('does not locally reject while runtime policy is unavailable', () => {
    expect(validatePasswordStrength('x', null)).toBeNull();
    expect(getPasswordPolicyHint(null)).toBe('密码须满足服务端安全策略');
  });
});

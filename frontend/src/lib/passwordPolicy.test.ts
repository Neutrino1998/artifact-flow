import { describe, test, expect } from 'vitest';
import {
  PASSWORD_MIN_LENGTH,
  validatePasswordStrength,
} from './passwordPolicy';

/**
 * 客户端强度提示(UX 用,后端权威)。镜像后端默认策略:≥8、字母+数字+符号三类全。
 */
describe('validatePasswordStrength', () => {
  test('accepts a strong password', () => {
    expect(validatePasswordStrength('Abcd123!')).toBeNull();
  });

  test('rejects too short', () => {
    const msg = validatePasswordStrength('Ab1!');
    expect(msg).toMatch(/长度/);
    expect('Ab1!'.length).toBeLessThan(PASSWORD_MIN_LENGTH);
  });

  test('rejects missing symbol', () => {
    expect(validatePasswordStrength('Abcd1234')).toMatch(/符号/);
  });

  test('rejects missing digit', () => {
    expect(validatePasswordStrength('Abcdefg!')).toMatch(/数字/);
  });

  test('rejects missing letter', () => {
    expect(validatePasswordStrength('1234567!')).toMatch(/字母/);
  });
});

/**
 * 客户端密码强度提示（门类三 C7）。
 *
 * 仅用于即时 UX —— 禁用提交按钮 + 行内提示。**后端是权威**:真正的强度
 * 校验、弱口令/键盘序列黑名单、不重用查重都在后端(返回 400/422 带具体原因)。
 * 这里只镜像「长度 + 字母+数字+符号 三类」这三条最基本的,避免把黑名单逻辑
 * 在前端重复维护(易漂移)。
 *
 * 与后端 config 默认值对齐:PASSWORD_MIN_LENGTH=8、字母+数字+符号三类全。
 */

export const PASSWORD_MIN_LENGTH = 8;

export const PASSWORD_POLICY_HINT = `至少 ${PASSWORD_MIN_LENGTH} 位，须同时包含字母、数字和符号`;

/** 返回不达标原因(中文,可直接展示);达标返回 null。 */
export function validatePasswordStrength(pw: string): string | null {
  if (pw.length < PASSWORD_MIN_LENGTH) {
    return `口令长度不足，至少需要 ${PASSWORD_MIN_LENGTH} 位`;
  }
  const missing: string[] = [];
  if (!/[A-Za-z]/.test(pw)) missing.push('字母');
  if (!/[0-9]/.test(pw)) missing.push('数字');
  if (!/[^A-Za-z0-9]/.test(pw)) missing.push('符号');
  if (missing.length > 0) {
    return `口令复杂度不足，必须同时包含${missing.join('、')}`;
  }
  return null;
}

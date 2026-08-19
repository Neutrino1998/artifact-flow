export function authProviderLabel(authProvider: string): string {
  return authProvider === 'local_password'
    ? '本地密码'
    : `SSO · ${authProvider}`;
}

export function bulkFailureLabel(reason: string): string {
  if (reason === 'forbidden_self') return '不能对当前账号执行此操作';
  if (reason === 'not_found') return '用户不存在';
  if (reason === 'profile_managed_by_provider') return '资料由认证来源维护';
  return '操作失败';
}

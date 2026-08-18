import { ApiError } from '@/lib/api';

export function captureAndClearCallbackQuery(
  currentUrl: string,
  replaceUrl: (cleanPath: string) => void,
): URLSearchParams {
  const url = new URL(currentUrl);
  const params = new URLSearchParams(url.search);
  // Drop both query and fragment. The provider contract uses query parameters,
  // but clearing the fragment too prevents an unexpected credential-shaped
  // value from lingering in browser history.
  replaceUrl(url.pathname);
  return params;
}

export function getSingleCallbackValue(
  params: URLSearchParams,
  name: string,
): string | null {
  const values = params.getAll(name);
  if (values.length !== 1 || !values[0]) return null;
  return values[0];
}

function withRequestId(message: string, error: ApiError): string {
  return error.requestId ? `${message}（错误码 ${error.requestId}）` : message;
}

export function ssoStartErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '无法连接认证服务，请稍后重试。';
  if (error.status === 429) return '操作过于频繁，请稍后再试。';
  if (error.status === 404) return '企业统一认证当前未启用。';
  if (error.status >= 500) {
    return withRequestId('企业统一认证暂时不可用，请稍后重试。', error);
  }
  return '无法发起企业统一认证，请重试。';
}

export function ssoCallbackErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '无法连接认证服务，请重新发起登录。';
  if (error.status === 400 || error.status === 401) {
    return '本次企业登录已失效或未完成，请重新发起登录。';
  }
  if (error.status === 404) return '企业统一认证当前未启用。';
  if (error.status >= 500) {
    return withRequestId('企业统一认证暂时不可用，请稍后重试。', error);
  }
  return '企业登录未能完成，请重新发起登录。';
}

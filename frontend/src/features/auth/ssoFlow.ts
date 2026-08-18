import { ApiError } from '@/lib/api';

export const SSO_CALLBACK_FAILURE_KEY = 'af_sso_callback_failure';

export type SsoCallbackFailureCode =
  | 'missing'
  | 'cancelled'
  | 'disabled'
  | 'expired'
  | 'unavailable'
  | 'failed'
  | 'network';

export interface SsoCallbackFailure {
  code: SsoCallbackFailureCode;
  requestId?: string;
}

const FAILURE_CODES = new Set<SsoCallbackFailureCode>([
  'missing',
  'cancelled',
  'disabled',
  'expired',
  'unavailable',
  'failed',
  'network',
]);

function safeRequestId(value: unknown): string | undefined {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value)
    ? value
    : undefined;
}

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

function failureWithRequestId(message: string, failure: SsoCallbackFailure): string {
  return failure.requestId ? `${message}（错误码 ${failure.requestId}）` : message;
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
  return ssoCallbackFailureMessage(ssoCallbackFailureFromError(error));
}

export function ssoCallbackFailureFromError(error: unknown): SsoCallbackFailure {
  if (!(error instanceof ApiError)) return { code: 'network' };
  if (error.status === 400 || error.status === 401) return { code: 'expired' };
  if (error.status === 404) return { code: 'disabled' };
  if (error.status >= 500) {
    return { code: 'unavailable', requestId: safeRequestId(error.requestId) };
  }
  return { code: 'failed' };
}

export function ssoCallbackFailureMessage(failure: SsoCallbackFailure): string {
  if (failure.code === 'missing') {
    return '本次企业登录信息缺失或已失效，请重新发起登录。';
  }
  if (failure.code === 'cancelled') {
    return '企业登录未完成，可能已取消。请重新发起登录。';
  }
  if (failure.code === 'disabled') return '企业统一认证当前未启用。';
  if (failure.code === 'expired') {
    return '本次企业登录已失效或未完成，请重新发起登录。';
  }
  if (failure.code === 'unavailable') {
    return failureWithRequestId(
      '企业统一认证暂时不可用，请稍后重试。',
      failure,
    );
  }
  if (failure.code === 'network') {
    return '无法连接认证服务，请重新发起登录。';
  }
  return '企业登录未能完成，请重新发起登录。';
}

/** Carry only a sanitized failure classification across the document reset. */
export function rememberSsoCallbackFailure(
  storage: Storage,
  failure: SsoCallbackFailure,
): void {
  try {
    const requestId = safeRequestId(failure.requestId);
    storage.setItem(SSO_CALLBACK_FAILURE_KEY, JSON.stringify({
      code: failure.code,
      ...(requestId ? { requestId } : {}),
    }));
  } catch {
    // The full-document navigation remains authoritative. If storage is
    // unavailable, the clean callback shows its generic expired-flow message.
  }
}

/** Read-once counterpart used by the clean callback document. */
export function takeSsoCallbackFailure(storage: Storage): SsoCallbackFailure | null {
  let raw: string | null = null;
  try {
    raw = storage.getItem(SSO_CALLBACK_FAILURE_KEY);
    storage.removeItem(SSO_CALLBACK_FAILURE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { code?: unknown; requestId?: unknown };
    if (typeof parsed.code !== 'string' || !FAILURE_CODES.has(parsed.code as SsoCallbackFailureCode)) {
      return null;
    }
    const requestId = safeRequestId(parsed.requestId);
    return {
      code: parsed.code as SsoCallbackFailureCode,
      ...(requestId ? { requestId } : {}),
    };
  } catch {
    return null;
  }
}

import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import {
  captureAndClearCallbackQuery,
  getSingleCallbackValue,
  rememberSsoCallbackFailure,
  SSO_CALLBACK_FAILURE_KEY,
  ssoCallbackErrorMessage,
  takeSsoCallbackFailure,
} from './ssoFlow';

describe('SSO callback primitives', () => {
  it('captures parameters and removes query and fragment from browser history', () => {
    const replace = vi.fn();
    const params = captureAndClearCallbackQuery(
      'https://app.example/auth/sso/callback?af_sso_state=s1&authorization_key=secret#extra',
      replace,
    );

    expect(replace).toHaveBeenCalledWith('/auth/sso/callback');
    expect(getSingleCallbackValue(params, 'af_sso_state')).toBe('s1');
    expect(getSingleCallbackValue(params, 'authorization_key')).toBe('secret');
  });

  it('rejects missing, blank, or duplicate callback values', () => {
    expect(getSingleCallbackValue(new URLSearchParams(), 'token')).toBeNull();
    expect(getSingleCallbackValue(new URLSearchParams('token='), 'token')).toBeNull();
    expect(
      getSingleCallbackValue(new URLSearchParams('token=a&token=b'), 'token'),
    ).toBeNull();
  });

  it('never exposes backend detail text in callback failures', () => {
    const error = new ApiError(
      503,
      'raw upstream detail that must stay hidden',
      undefined,
      'req-safe',
    );
    const message = ssoCallbackErrorMessage(error);
    expect(message).toContain('req-safe');
    expect(message).not.toContain('raw upstream');
  });

  it('carries only a validated, read-once failure classification across documents', () => {
    window.sessionStorage.clear();
    rememberSsoCallbackFailure(window.sessionStorage, {
      code: 'unavailable',
      requestId: 'req-safe',
    });
    const raw = window.sessionStorage.getItem(SSO_CALLBACK_FAILURE_KEY);
    expect(raw).toBe('{"code":"unavailable","requestId":"req-safe"}');
    expect(takeSsoCallbackFailure(window.sessionStorage)).toEqual({
      code: 'unavailable',
      requestId: 'req-safe',
    });
    expect(window.sessionStorage.getItem(SSO_CALLBACK_FAILURE_KEY)).toBeNull();
  });
});

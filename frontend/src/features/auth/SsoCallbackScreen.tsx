'use client';

import { useEffect, useRef, useState } from 'react';
import { exchangeSso, getAuthConfig, startSso } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { APP_NAME } from '@/lib/branding';
import {
  captureAndClearCallbackQuery,
  getSingleCallbackValue,
  rememberSsoCallbackFailure,
  ssoCallbackFailureFromError,
  ssoCallbackFailureMessage,
  ssoStartErrorMessage,
  takeSsoCallbackFailure,
  type SsoCallbackFailure,
} from './ssoFlow';

const replaceCurrentDocument = (url: string) => window.location.replace(url);

function getSessionStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

interface SsoCallbackScreenProps {
  replaceDocument?: (url: string) => void;
}

export default function SsoCallbackScreen({
  replaceDocument = replaceCurrentDocument,
}: SsoCallbackScreenProps) {
  const authLogin = useAuthStore((s) => s.login);
  const startedRef = useRef(false);
  const activeRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    activeRef.current = true;
    if (!startedRef.current) {
      startedRef.current = true;
      const params = captureAndClearCallbackQuery(
        window.location.href,
        (cleanPath) => window.history.replaceState(window.history.state, '', cleanPath),
      );
      const callbackStorage = getSessionStorage();

      const finishFailure = (failure: SsoCallbackFailure) => {
        try {
          if (callbackStorage) rememberSsoCallbackFailure(callbackStorage, failure);
        } finally {
          replaceDocument('/auth/sso/callback');
        }
      };

      void (async () => {
        const carriedFailure = callbackStorage
          ? takeSsoCallbackFailure(callbackStorage)
          : null;
        if (params.size === 0 && carriedFailure) {
          if (activeRef.current) setError(ssoCallbackFailureMessage(carriedFailure));
          return;
        }

        const state = getSingleCallbackValue(params, 'af_sso_state');
        if (!state) {
          if (activeRef.current) {
            if (params.size > 0) finishFailure({ code: 'missing' });
            else setError(ssoCallbackFailureMessage({ code: 'missing' }));
          }
          return;
        }

        try {
          const config = await getAuthConfig();
          const tokenParam = config.sso.enabled ? config.sso.token_param : null;
          if (!tokenParam) {
            if (activeRef.current) finishFailure({ code: 'disabled' });
            return;
          }
          const upstreamToken = getSingleCallbackValue(params, tokenParam);
          if (!upstreamToken) {
            if (activeRef.current) finishFailure({ code: 'cancelled' });
            return;
          }
          const result = await exchangeSso({ state, upstream_token: upstreamToken });
          if (!activeRef.current) return;
          authLogin(result.access_token, result.user);
          replaceDocument('/');
        } catch (err) {
          if (activeRef.current) finishFailure(ssoCallbackFailureFromError(err));
        }
      })();
    }

    return () => { activeRef.current = false; };
  }, [authLogin, replaceDocument]);

  async function handleRetry() {
    setRetrying(true);
    setError(null);
    try {
      const result = await startSso();
      window.location.assign(result.authorization_url);
    } catch (err) {
      setError(ssoStartErrorMessage(err));
      setRetrying(false);
    }
  }

  return (
    <div className="flex min-h-screen [min-height:100dvh] items-center justify-center bg-bg dark:bg-bg-dark px-4">
      <div className="w-full max-w-sm rounded-card bg-surface dark:bg-surface-dark p-6 text-center shadow-modal sm:p-8">
        <h1 className="text-2xl font-semibold font-serif text-text-primary dark:text-text-primary-dark">
          {APP_NAME}
        </h1>
        {error ? (
          <>
            <p className="mt-5 text-sm leading-relaxed text-status-error">{error}</p>
            <button
              type="button"
              onClick={handleRetry}
              disabled={retrying}
              className="mt-5 w-full rounded-lg bg-accent py-2 font-medium text-white hover:bg-accent-hover disabled:opacity-50"
            >
              {retrying ? '正在跳转...' : '重新发起企业统一认证'}
            </button>
          </>
        ) : (
          <p className="mt-5 text-sm text-text-secondary dark:text-text-secondary-dark">
            正在完成企业登录...
          </p>
        )}
      </div>
    </div>
  );
}

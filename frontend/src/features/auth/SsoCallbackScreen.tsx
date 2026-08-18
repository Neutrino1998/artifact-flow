'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { exchangeSso, getAuthConfig, startSso } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { APP_NAME } from '@/lib/branding';
import {
  captureAndClearCallbackQuery,
  getSingleCallbackValue,
  ssoCallbackErrorMessage,
  ssoStartErrorMessage,
} from './ssoFlow';

export default function SsoCallbackScreen() {
  const router = useRouter();
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

      void (async () => {
        const state = getSingleCallbackValue(params, 'af_sso_state');
        if (!state) {
          if (activeRef.current) {
            setError('本次企业登录信息缺失或已失效，请重新发起登录。');
          }
          return;
        }

        try {
          const config = await getAuthConfig();
          const tokenParam = config.sso.enabled ? config.sso.token_param : null;
          if (!tokenParam) {
            if (activeRef.current) setError('企业统一认证当前未启用。');
            return;
          }
          const upstreamToken = getSingleCallbackValue(params, tokenParam);
          if (!upstreamToken) {
            if (activeRef.current) {
              setError('企业登录未完成，可能已取消。请重新发起登录。');
            }
            return;
          }
          const result = await exchangeSso({ state, upstream_token: upstreamToken });
          if (!activeRef.current) return;
          authLogin(result.access_token, result.user);
          router.replace('/');
        } catch (err) {
          if (activeRef.current) setError(ssoCallbackErrorMessage(err));
        }
      })();
    }

    return () => { activeRef.current = false; };
  }, [authLogin, router]);

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

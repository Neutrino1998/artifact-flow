'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { getAuthConfig, login, startSso } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import type { AuthPublicConfigResponse } from '@/types';
import { APP_NAME, APP_TAGLINE } from '@/lib/branding';
import BrandingFooter from '@/components/BrandingFooter';
import { ssoStartErrorMessage } from './ssoFlow';

interface LoginScreenProps {
  navigateTo?: (url: string) => void;
}

export default function LoginScreen({
  navigateTo = (url) => window.location.assign(url),
}: LoginScreenProps) {
  const router = useRouter();
  const authLogin = useAuthStore((s) => s.login);
  const storedNotice = useAuthStore((s) => s.notice);
  const clearNotice = useAuthStore((s) => s.clearNotice);
  const [initialNotice] = useState(storedNotice);
  const [authConfig, setAuthConfig] = useState<AuthPublicConfigResponse | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [ssoError, setSsoError] = useState('');
  const [ssoLoading, setSsoLoading] = useState(false);

  useEffect(() => {
    clearNotice();
    let cancelled = false;
    getAuthConfig()
      .then((config) => { if (!cancelled) setAuthConfig(config); })
      .catch(() => {
        // Public SSO discovery is best-effort for the page. The local emergency
        // login remains usable and unchanged if discovery is temporarily down.
      });
    return () => { cancelled = true; };
  }, [clearNotice]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await login({ username, password });
      authLogin(res.access_token, res.user);
      router.push('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  async function handleSsoStart() {
    setSsoError('');
    setSsoLoading(true);
    try {
      const result = await startSso();
      navigateTo(result.authorization_url);
    } catch (err) {
      setSsoError(ssoStartErrorMessage(err));
      setSsoLoading(false);
    }
  }

  const sso = authConfig?.sso.enabled ? authConfig.sso : null;

  return (
    <div className="flex min-h-screen [min-height:100dvh] flex-col items-center justify-center bg-bg dark:bg-bg-dark px-4 py-[env(safe-area-inset-top)]">
      <div className="w-full max-w-sm rounded-card bg-surface dark:bg-surface-dark p-6 sm:p-8 shadow-modal">
        <h1 className="text-center text-3xl font-semibold font-serif text-text-primary dark:text-text-primary-dark">
          {APP_NAME}
        </h1>
        <p className="mb-6 mt-1 text-center text-sm text-text-secondary dark:text-text-secondary-dark">
          {APP_TAGLINE}
        </p>

        {initialNotice === 'session_expired' && (
          <p className="mb-4 rounded-lg bg-status-warning/10 px-3 py-2 text-sm text-status-warning">
            登录已失效，请重新登录。
          </p>
        )}

        {sso && (
          <div className="mb-5">
            <button
              type="button"
              onClick={handleSsoStart}
              disabled={ssoLoading}
              className="w-full rounded-lg bg-accent py-2 font-medium text-white hover:bg-accent-hover disabled:opacity-50"
            >
              {ssoLoading ? '正在跳转...' : (sso.display_name || '企业统一认证')}
            </button>
            {ssoError && <p className="mt-2 text-sm text-status-error">{ssoError}</p>}
            <div className="my-5 flex items-center gap-3 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              <span className="h-px flex-1 bg-border dark:bg-border-dark" />
              <span>或使用本地账号</span>
              <span className="h-px flex-1 bg-border dark:bg-border-dark" />
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="username" className="mb-1 block text-text-secondary dark:text-text-secondary-dark">
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus={!sso}
              className="w-full rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-text-primary dark:text-text-primary-dark outline-none focus:border-accent dark:focus:border-accent"
            />
          </div>

          <div>
            <label htmlFor="password" className="mb-1 block text-text-secondary dark:text-text-secondary-dark">
              Password
            </label>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                className="w-full rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark py-2 pl-3 pr-14 text-text-primary dark:text-text-primary-dark outline-none focus:border-accent dark:focus:border-accent"
              />
              <button
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
                className="absolute inset-y-0 right-0 flex w-14 select-none items-center justify-center rounded-r-lg text-xs font-medium text-text-tertiary hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent dark:text-text-tertiary-dark dark:hover:text-accent"
              >
                {showPassword ? '隐藏' : '显示'}
              </button>
            </div>
          </div>

          {error && <p className="text-status-error">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className={`${sso ? 'border border-border dark:border-border-dark bg-bg dark:bg-bg-dark text-text-primary dark:text-text-primary-dark hover:bg-panel dark:hover:bg-panel-accent-dark' : 'bg-accent text-white hover:bg-accent-hover'} w-full rounded-lg py-2 font-medium disabled:opacity-50`}
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </div>
      <BrandingFooter variant="login" />
    </div>
  );
}

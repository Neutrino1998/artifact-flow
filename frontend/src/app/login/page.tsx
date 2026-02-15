'use client';

import { useState, FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { login } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';

export default function LoginPage() {
  const router = useRouter();
  const authLogin = useAuthStore((s) => s.login);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

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

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg dark:bg-bg-dark">
      <div className="w-full max-w-sm rounded-card bg-surface dark:bg-surface-dark p-8 shadow-modal">
        <h1 className="mb-6 text-center text-xl font-semibold text-text-primary dark:text-text-primary-dark">
          ArtifactFlow
        </h1>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="username"
              className="mb-1 block text-sm text-text-secondary dark:text-text-secondary-dark"
            >
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
              className="w-full rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-sm text-text-primary dark:text-text-primary-dark outline-none focus:border-accent"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="mb-1 block text-sm text-text-secondary dark:text-text-secondary-dark"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full rounded-lg border border-border dark:border-border-dark bg-bg dark:bg-bg-dark px-3 py-2 text-sm text-text-primary dark:text-text-primary-dark outline-none focus:border-accent"
            />
          </div>

          {error && (
            <p className="text-sm text-status-error">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-accent py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LoginScreen from './LoginScreen';
import { useAuthStore } from '@/stores/authStore';

const mocks = vi.hoisted(() => ({
  getAuthConfig: vi.fn(),
  startSso: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/components/BrandingFooter', () => ({ default: () => null }));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getAuthConfig: mocks.getAuthConfig,
  startSso: mocks.startSso,
  login: vi.fn(),
}));

describe('LoginScreen SSO discovery', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    window.localStorage.clear();
    useAuthStore.setState({ notice: null });
    mocks.getAuthConfig.mockReset();
    mocks.startSso.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('shows SSO as the primary entry while keeping the local form visible', async () => {
    mocks.getAuthConfig.mockResolvedValue({
      password_login_enabled: true,
      sso: {
        enabled: true,
        provider_id: 'enterprise_sso',
        display_name: '企业统一认证',
        token_param: 'authorization_key',
      },
    });
    mocks.startSso.mockResolvedValue({
      authorization_url: 'https://identity.example/login',
      expires_in: 300,
    });
    const navigate = vi.fn();

    await act(async () => {
      root.render(<LoginScreen navigateTo={navigate} />);
      await Promise.resolve();
    });

    expect(container.textContent).toContain('企业统一认证');
    expect(container.textContent).toContain('或使用本地账号');
    expect(container.querySelector('#username')).not.toBeNull();
    const ssoButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === '企业统一认证',
    );
    await act(async () => {
      ssoButton?.click();
      await Promise.resolve();
    });
    expect(navigate).toHaveBeenCalledWith('https://identity.example/login');
  });

  it('keeps the legacy local form when the provider is disabled', async () => {
    mocks.getAuthConfig.mockResolvedValue({
      password_login_enabled: true,
      sso: {
        enabled: false,
        provider_id: null,
        display_name: null,
        token_param: null,
      },
    });
    await act(async () => {
      root.render(<LoginScreen />);
      await Promise.resolve();
    });
    expect(container.textContent).not.toContain('或使用本地账号');
    expect(container.textContent).toContain('Sign in');
  });
});

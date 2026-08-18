import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SsoCallbackScreen from './SsoCallbackScreen';
import { useAuthStore } from '@/stores/authStore';

const mocks = vi.hoisted(() => ({
  getAuthConfig: vi.fn(),
  exchangeSso: vi.fn(),
  startSso: vi.fn(),
  router: { replace: vi.fn() },
}));

vi.mock('next/navigation', () => ({ useRouter: () => mocks.router }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getAuthConfig: mocks.getAuthConfig,
  exchangeSso: mocks.exchangeSso,
  startSso: mocks.startSso,
}));

const remoteUser = {
  id: 'user-remote',
  username: 'same-name',
  display_name: 'SSO Test User',
  role: 'user',
  auth_provider: 'enterprise_sso',
  can_change_password: false,
  can_edit_profile: false,
  must_change_password: false,
  department_path: ['一级', '二级'],
};

describe('SsoCallbackScreen', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    window.localStorage.clear();
    useAuthStore.setState({
      token: null,
      user: null,
      isAuthenticated: false,
      notice: null,
    });
    mocks.getAuthConfig.mockReset();
    mocks.exchangeSso.mockReset();
    mocks.startSso.mockReset();
    mocks.router.replace.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it('cleans the URL before exchange and persists only the ArtifactFlow session', async () => {
    const upstreamToken = 'upstream-secret-never-store';
    window.history.replaceState(
      {},
      '',
      `/auth/sso/callback?af_sso_state=state-1&authorization_key=${upstreamToken}`,
    );
    const order: string[] = [];
    const realReplace = window.history.replaceState.bind(window.history);
    vi.spyOn(window.history, 'replaceState').mockImplementation((...args) => {
      order.push('replace');
      realReplace(...args);
    });
    mocks.getAuthConfig.mockImplementation(async () => {
      order.push('config');
      expect(window.location.search).toBe('');
      return {
        password_login_enabled: true,
        sso: {
          enabled: true,
          provider_id: 'enterprise_sso',
          display_name: '企业统一认证',
          token_param: 'authorization_key',
        },
      };
    });
    mocks.exchangeSso.mockImplementation(async () => {
      order.push('exchange');
      expect(window.location.search).toBe('');
      return {
        access_token: 'artifactflow-jwt',
        token_type: 'bearer',
        expires_in: 28800,
        user: remoteUser,
      };
    });

    await act(async () => {
      root.render(<SsoCallbackScreen />);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(order).toEqual(['replace', 'config', 'exchange']);
    expect(mocks.exchangeSso).toHaveBeenCalledWith({
      state: 'state-1',
      upstream_token: upstreamToken,
    });
    expect(mocks.router.replace).toHaveBeenCalledWith('/');
    expect(window.localStorage.getItem('af_token')).toBe('artifactflow-jwt');
    expect(window.localStorage.getItem('af_user')).not.toContain(upstreamToken);
    expect(document.cookie).not.toContain(upstreamToken);
    expect(container.textContent).not.toContain(upstreamToken);
  });

  it('treats a refreshed, queryless callback as an expired flow', async () => {
    window.history.replaceState({}, '', '/auth/sso/callback');
    await act(async () => {
      root.render(<SsoCallbackScreen />);
      await Promise.resolve();
    });
    expect(mocks.getAuthConfig).not.toHaveBeenCalled();
    expect(mocks.exchangeSso).not.toHaveBeenCalled();
    expect(container.textContent).toContain('信息缺失或已失效');
    expect(container.textContent).toContain('重新发起企业统一认证');
  });
});

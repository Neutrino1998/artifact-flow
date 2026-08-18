import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UserResponse } from '@/types';
import { useAuthStore } from '@/stores/authStore';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';
import UserDetailForm from './UserDetailForm';

const apiMocks = vi.hoisted(() => ({
  getUser: vi.fn(),
  updateUser: vi.fn(),
  getUserImpact: vi.fn(),
  deleteUser: vi.fn(),
}));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  ...apiMocks,
}));
vi.mock('@/features/admin/departments/DepartmentCascader', () => ({
  default: ({ disabled }: { disabled?: boolean }) => (
    <button type="button" aria-label="部门选择" disabled={disabled}>部门选择</button>
  ),
}));
vi.mock('@/components/layout/DangerConfirmModal', () => ({
  default: ({ message, children }: { message: string; children?: React.ReactNode }) => (
    <div data-testid="danger-confirm">{children}<span>{message}</span></div>
  ),
  DangerConfirmTarget: ({ name, description }: { name: string; description?: string }) => (
    <div>{name} {description}</div>
  ),
}));

const remoteUser: UserResponse = {
  id: 'remote-user-id',
  username: 'shared-name',
  display_name: '统一认证用户',
  role: 'user',
  is_active: true,
  auth_provider: 'company-sso',
  can_change_password: false,
  can_edit_profile: false,
  department_id: 'department-1',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
};

describe('UserDetailForm provider-managed identity', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    apiMocks.getUser.mockReset().mockResolvedValue(remoteUser);
    apiMocks.updateUser.mockReset().mockImplementation(async (_id, patch) => ({
      ...remoteUser,
      ...patch,
    }));
    apiMocks.getUserImpact.mockReset().mockResolvedValue({ conversation_count: 3 });
    apiMocks.deleteUser.mockReset().mockResolvedValue(undefined);
    useUIStore.setState(INITIAL_UI_STATE);
    useAuthStore.setState({
      user: {
        id: 'current-admin-id',
        username: 'admin',
        display_name: 'Admin',
        role: 'admin',
        auth_provider: 'local_password',
        can_change_password: true,
        can_edit_profile: true,
        must_change_password: false,
        department_path: null,
      },
      token: 'admin-token',
      isAuthenticated: true,
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useUIStore.setState(INITIAL_UI_STATE);
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });
  });

  it('keeps provider facts read-only while allowing local authorization edits and warning on delete', async () => {
    await act(async () => {
      root.render(<UserDetailForm userId={remoteUser.id} />);
      await Promise.resolve();
    });

    expect(container.textContent).toContain('SSO · company-sso');
    expect(container.textContent).toContain('显示名和部门由企业认证维护');
    expect(container.querySelector<HTMLInputElement>('input[type="text"]')?.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="部门选择"]')?.disabled).toBe(true);
    expect(container.querySelector('input[type="password"]')).toBeNull();

    const roleSelect = container.querySelector<HTMLSelectElement>('select');
    expect(roleSelect?.disabled).toBe(false);
    await act(async () => {
      if (!roleSelect) throw new Error('role select not rendered');
      roleSelect.value = 'admin';
      roleSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const save = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === '保存',
    );
    await act(async () => {
      save?.click();
      await Promise.resolve();
    });
    expect(apiMocks.updateUser).toHaveBeenCalledWith(remoteUser.id, { role: 'admin' });

    const remove = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === '删除用户',
    );
    await act(async () => {
      remove?.click();
      await Promise.resolve();
    });
    expect(container.textContent).toContain('下次 SSO 登录时会以新的内部 ID 重新创建');
  });
});

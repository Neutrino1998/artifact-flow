import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PersonalAccessTokenDialog from './PersonalAccessTokenDialog';

const listPersonalAccessTokens = vi.fn();
const createPersonalAccessToken = vi.fn();
const revokePersonalAccessToken = vi.fn();

vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {},
  listPersonalAccessTokens: (...args: unknown[]) => listPersonalAccessTokens(...args),
  createPersonalAccessToken: (...args: unknown[]) => createPersonalAccessToken(...args),
  revokePersonalAccessToken: (...args: unknown[]) => revokePersonalAccessToken(...args),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('PersonalAccessTokenDialog', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    listPersonalAccessTokens.mockReset();
    createPersonalAccessToken.mockReset();
    revokePersonalAccessToken.mockReset();
    listPersonalAccessTokens.mockResolvedValue({ tokens: [] });
    createPersonalAccessToken.mockResolvedValue({
      id: 'pat_123',
      name: '分析脚本',
      prefix: 'af_pat_12345678…',
      scopes: ['conversations:read', 'conversations:write', 'artifacts:read'],
      created_at: '2026-08-21T00:00:00',
      expires_at: '2026-11-19T00:00:00',
      last_used_at: null,
      revoked_at: null,
      token: 'af_pat_1234567890abcdef_secret-value',
    });
    revokePersonalAccessToken.mockResolvedValue(undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('creates a scoped token and reveals the bearer once', async () => {
    await act(async () => {
      root.render(<PersonalAccessTokenDialog onClose={vi.fn()} />);
      await Promise.resolve();
    });

    const nameInput = document.body.querySelector<HTMLInputElement>('input[placeholder="例如：数据分析脚本"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(nameInput, '分析脚本');
      nameInput?.dispatchEvent(new Event('input', { bubbles: true }));
    });

    const createButton = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '创建密钥',
    );
    await act(async () => {
      createButton?.click();
      await Promise.resolve();
    });

    expect(createPersonalAccessToken).toHaveBeenCalledWith({
      name: '分析脚本',
      scopes: ['conversations:read', 'conversations:write', 'artifacts:read'],
      expires_in_days: 90,
    });
    expect(document.body.textContent).toContain('立即复制并妥善保存');
    expect(document.body.textContent).toContain('af_pat_1234567890abcdef_secret-value');
    expect(document.body.querySelector('[aria-label="复制 API 密钥"]')).not.toBeNull();
  });

  it('uses a plain numeric text field without browser stepper controls', async () => {
    await act(async () => {
      root.render(<PersonalAccessTokenDialog onClose={vi.fn()} />);
      await Promise.resolve();
    });

    const expiryInput = document.body.querySelector<HTMLInputElement>('#pat-expiry-days');
    expect(expiryInput?.type).toBe('text');
    expect(expiryInput?.inputMode).toBe('numeric');
    expect(expiryInput?.value).toBe('90');
    expect(document.body.textContent).toContain('最长 365 天');
    expect(document.body.textContent).toContain('Authorization: Bearer <PAT>');
    expect(document.body.textContent).toContain('只可调用普通用户 API');
    const readConversationScope = document.body.querySelector<HTMLInputElement>(
      'input[aria-label="权限范围：读取对话"]',
    );
    expect(readConversationScope?.className).toContain('appearance-none');
    expect(readConversationScope?.className).not.toContain('accent-accent');
  });

  it('does not allow creation before the initial token list settles', async () => {
    let resolveList!: (value: { tokens: [] }) => void;
    listPersonalAccessTokens.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));

    await act(async () => {
      root.render(<PersonalAccessTokenDialog onClose={vi.fn()} />);
    });

    const nameInput = document.body.querySelector<HTMLInputElement>(
      'input[placeholder="例如：数据分析脚本"]',
    );
    const createButton = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '创建密钥',
    );
    expect(nameInput?.disabled).toBe(true);
    expect(createButton?.hasAttribute('disabled')).toBe(true);

    await act(async () => {
      resolveList({ tokens: [] });
      await Promise.resolve();
    });

    expect(nameInput?.disabled).toBe(false);
  });
});

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LoginPage from './page';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/components/BrandingFooter', () => ({
  default: () => null,
}));

describe('LoginPage', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('toggles password visibility without submitting the form', async () => {
    await act(async () => {
      root.render(<LoginPage />);
    });

    const input = container.querySelector<HTMLInputElement>('#password');
    const showButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="显示密码"]',
    );

    expect(input?.type).toBe('password');
    expect(showButton?.type).toBe('button');
    expect(showButton?.textContent).toBe('显示');
    expect(showButton?.hasAttribute('aria-pressed')).toBe(false);

    await act(async () => {
      showButton?.click();
    });

    expect(input?.type).toBe('text');
    expect(showButton?.getAttribute('aria-label')).toBe('隐藏密码');
    expect(showButton?.textContent).toBe('隐藏');

    await act(async () => {
      showButton?.click();
    });

    expect(input?.type).toBe('password');
    expect(showButton?.getAttribute('aria-label')).toBe('显示密码');
    expect(showButton?.textContent).toBe('显示');
  });
});

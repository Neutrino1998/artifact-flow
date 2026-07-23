import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ThreeColumnLayout from './ThreeColumnLayout';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';

vi.mock('@/hooks/useMediaQuery', () => ({
  BREAKPOINTS: {
    md: '(min-width: 768px)',
    lg: '(min-width: 1024px)',
    xl: '(min-width: 1280px)',
  },
  useMediaQuery: () => false,
}));

describe('ThreeColumnLayout mobile drawer', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useUIStore.setState(INITIAL_UI_STATE);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useUIStore.setState(INITIAL_UI_STATE);
  });

  it('renders the full drawer presentation and closes through the shared navigation callback', async () => {
    await act(async () => {
      root.render(
        <ThreeColumnLayout
          sidebar={({ variant, onNavigate }) => (
            <div>
              <span data-testid="sidebar-variant">{variant}</span>
              <button onClick={onNavigate}>Navigate</button>
            </div>
          )}
          chat={<div>Chat</div>}
        />,
      );
    });

    const open = container.querySelector<HTMLButtonElement>('button[aria-label="展开侧栏"]');
    expect(open).not.toBeNull();
    expect(open?.querySelector('rect')).not.toBeNull();
    expect(open?.querySelector('path[d="M6 1.5v13"]')).not.toBeNull();

    await act(async () => open?.click());

    expect(container.querySelector('[data-testid="sidebar-variant"]')?.textContent).toBe('drawer');
    const drawer = container.querySelector('aside[aria-label="主菜单"]');
    expect(drawer).not.toBeNull();
    expect(drawer?.firstElementChild?.className).toContain('rounded-card');
    expect(container.querySelector('button[aria-label="展开侧栏"]')).toBeNull();

    const navigate = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === 'Navigate',
    );
    await act(async () => navigate?.click());

    expect(container.querySelector('aside[aria-label="主菜单"]')).toBeNull();
    expect(container.querySelector('button[aria-label="展开侧栏"]')).not.toBeNull();
  });

  it('provides an explicit close action for the mobile artifact drawer', async () => {
    useUIStore.setState({ artifactPanelVisible: true });

    await act(async () => {
      root.render(
        <ThreeColumnLayout
          sidebar={<div>Sidebar</div>}
          chat={<div>Chat</div>}
          artifact={<div>Artifact</div>}
        />,
      );
    });

    const close = container.querySelector<HTMLButtonElement>(
      'button[aria-label="关闭文件面板"]',
    );
    expect(close).not.toBeNull();

    await act(async () => close?.click());

    expect(useUIStore.getState().artifactPanelVisible).toBe(false);
    expect(container.querySelector('button[aria-label="关闭文件面板"]')).toBeNull();
  });
});

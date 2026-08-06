import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ArtifactDetail, ArtifactSummary } from '@/types';
import { useArtifactStore } from '@/stores/artifactStore';
import ArtifactFileTabs from './ArtifactFileTabs';

const { selectArtifact } = vi.hoisted(() => ({ selectArtifact: vi.fn() }));

vi.mock('@/hooks/useArtifacts', () => ({
  useArtifacts: () => ({ selectArtifact }),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function summary(id: string): ArtifactSummary {
  return {
    id,
    content_type: 'text/markdown',
    title: `Document ${id}`,
    current_version: 1,
    source: 'agent',
    original_filename: null,
    has_blob: false,
    created_at: '2026-08-06T00:00:00',
    updated_at: '2026-08-06T00:00:00',
  } as ArtifactSummary;
}

function detail(id: string): ArtifactDetail {
  return {
    ...summary(id),
    session_id: 'session-1',
    content: 'body',
    versions: [],
  } as ArtifactDetail;
}

describe('ArtifactFileTabs', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    selectArtifact.mockReset();
    useArtifactStore.getState().reset();
    useArtifactStore.getState().setArtifacts([summary('A'), summary('B')]);
    useArtifactStore.getState().setCurrent(detail('A'));
    useArtifactStore.getState().setCurrent(detail('B'));
    useArtifactStore.getState().setCurrent(detail('A'));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useArtifactStore.getState().reset();
  });

  it('switches files through the existing artifact selection path', async () => {
    await act(async () => root.render(<ArtifactFileTabs />));

    const activeTab = container.querySelector<HTMLButtonElement>(
      'button[role="tab"][aria-selected="true"]',
    )?.parentElement;
    expect(activeTab?.className).toContain('border-accent');
    expect(activeTab?.querySelector('span.bg-accent')).toBeNull();

    const tab = container.querySelector<HTMLButtonElement>(
      'button[role="tab"][title="Document B"]',
    );
    await act(async () => tab?.click());

    expect(selectArtifact).toHaveBeenCalledWith('B');
  });

  it('closing the active tab selects its adjacent open file', async () => {
    await act(async () => root.render(<ArtifactFileTabs />));

    const close = container.querySelector<HTMLButtonElement>(
      'button[aria-label="关闭 Document A"]',
    );
    await act(async () => close?.click());

    expect(useArtifactStore.getState().openArtifactIds).toEqual(['B']);
    expect(useArtifactStore.getState().current).toBeNull();
    expect(selectArtifact).toHaveBeenCalledWith('B');
  });

  it('scrolls the selected tab into view in either direction without reordering tabs', async () => {
    await act(async () => root.render(<ArtifactFileTabs />));

    const tabList = container.querySelector<HTMLDivElement>('[role="tablist"]');
    const tabA = container.querySelector<HTMLButtonElement>(
      'button[role="tab"][title="Document A"]',
    )?.parentElement;
    const tabB = container.querySelector<HTMLButtonElement>(
      'button[role="tab"][title="Document B"]',
    )?.parentElement;
    expect(tabList).not.toBeNull();
    expect(tabA).not.toBeNull();
    expect(tabB).not.toBeNull();

    vi.spyOn(tabList!, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      right: 120,
    } as DOMRect);
    vi.spyOn(tabA!, 'getBoundingClientRect').mockReturnValue({
      left: -80,
      right: 20,
    } as DOMRect);
    vi.spyOn(tabB!, 'getBoundingClientRect').mockReturnValue({
      left: 160,
      right: 260,
    } as DOMRect);
    const scrollBy = vi.fn();
    Object.defineProperty(tabList, 'scrollBy', { configurable: true, value: scrollBy });

    await act(async () => useArtifactStore.getState().setCurrent(detail('B')));
    expect(scrollBy).toHaveBeenCalledWith({ left: 148, behavior: 'smooth' });

    scrollBy.mockClear();
    await act(async () => useArtifactStore.getState().setCurrent(detail('A')));
    expect(scrollBy).toHaveBeenCalledWith({ left: -88, behavior: 'smooth' });
    expect(useArtifactStore.getState().openArtifactIds).toEqual(['A', 'B']);
  });
});

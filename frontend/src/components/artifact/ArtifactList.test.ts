import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ArtifactSummary } from '@/types';
import { useArtifactStore } from '@/stores/artifactStore';
import ArtifactList, { groupArtifactsBySource } from './ArtifactList';
import { artifactFileTypeLabel } from './ArtifactFileIcon';

const { selectArtifact } = vi.hoisted(() => ({ selectArtifact: vi.fn() }));

vi.mock('@/hooks/useArtifacts', () => ({
  useArtifacts: () => ({ selectArtifact }),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function artifact(id: string, source: string | null): ArtifactSummary {
  return {
    id,
    source,
    title: id,
    content_type: 'text/plain',
    current_version: 1,
    original_filename: null,
    has_blob: false,
    created_at: '2026-08-06T00:00:00',
    updated_at: '2026-08-06T00:00:00',
  };
}

describe('groupArtifactsBySource', () => {
  it('uses the four runtime artifact sources in stable UI order', () => {
    const groups = groupArtifactsBySource([
      artifact('tool-file', 'tool'),
      artifact('upload-file', 'user_upload'),
      artifact('sandbox-file', 'sandbox'),
      artifact('agent-file', 'agent'),
    ]);

    expect(groups.map((group) => [group.key, group.label])).toEqual([
      ['agent', 'Agent'],
      ['user_upload', 'Uploads'],
      ['sandbox', 'Sandbox'],
      ['tool', 'Tools'],
    ]);
  });

  it('keeps unknown or missing future sources visible under Other', () => {
    const groups = groupArtifactsBySource([
      artifact('missing', null),
      artifact('future', 'connector'),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('other');
    expect(groups[0].artifacts.map((item) => item.id)).toEqual(['missing', 'future']);
  });
});

describe('artifactFileTypeLabel', () => {
  it('uses compact MIME labels and falls back to the filename extension', () => {
    expect(artifactFileTypeLabel('text/markdown')).toBe('MD');
    expect(
      artifactFileTypeLabel(
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      ),
    ).toBe('DOCX');
    expect(artifactFileTypeLabel('application/octet-stream', 'archive.tar')).toBe('TAR');
    expect(artifactFileTypeLabel('application/octet-stream')).toBe('BIN');
  });
});

describe('ArtifactList source tree', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    selectArtifact.mockReset();
    useArtifactStore.getState().reset();
    useArtifactStore.getState().setArtifacts([
      {
        ...artifact('report', 'agent'),
        title: 'Quarterly report',
        content_type: 'text/markdown',
        current_version: 7,
      },
    ]);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useArtifactStore.getState().reset();
  });

  it('shows a compact type label but keeps version detail out of the tree', async () => {
    useArtifactStore.getState().addPendingUpdate('report');
    await act(async () => root.render(createElement(ArtifactList)));

    expect(container.querySelector('h3')?.textContent).toBe('文件面板');
    const headingIcon = container
      .querySelector('h3')
      ?.parentElement?.querySelector<SVGElement>('svg.lucide-folders');
    expect(headingIcon?.getAttribute('width')).toBe('16');
    expect(headingIcon?.parentElement?.className).toContain('text-text-primary');

    const fileRow = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Quarterly report'),
    );
    expect(fileRow?.className).toContain('h-10');
    expect(fileRow?.textContent).toContain('MD');
    expect(fileRow?.textContent).not.toContain('v7');
    expect(fileRow?.querySelector('svg.tabler-icon-file-text')).not.toBeNull();
    expect(fileRow?.querySelector('[aria-label="本回合已更新"]')?.className).toContain(
      'bg-accent',
    );

    await act(async () => fileRow?.click());
    expect(selectArtifact).toHaveBeenCalledWith('report');
  });

  it('uses a screenshot-style chevron and collapses the source group', async () => {
    await act(async () => root.render(createElement(ArtifactList)));

    const group = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Agent'),
    );
    const chevron = group?.querySelector<HTMLSpanElement>('span.border-b.border-r');
    expect(chevron?.className).toContain('rotate-45');
    const folderIcon = group?.querySelector<SVGElement>('svg.tabler-icon-folder');
    expect(folderIcon).not.toBeNull();
    expect(folderIcon?.getAttribute('class')).toContain('h-5');
    expect(container.querySelector('#artifact-source-agent')?.className).toContain('ml-[15px]');

    await act(async () => group?.click());
    expect(group?.getAttribute('aria-expanded')).toBe('false');
    expect(container.textContent).not.toContain('Quarterly report');
  });
});

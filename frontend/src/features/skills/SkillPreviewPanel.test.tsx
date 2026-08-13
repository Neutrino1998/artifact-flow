import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { INITIAL_UI_STATE, useUIStore } from '@/stores/uiStore';
import SkillPreviewPanel from './SkillPreviewPanel';

const apiMock = vi.hoisted(() => ({
  getSkillDetail: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  getSkillDetail: apiMock.getSkillDetail,
}));

vi.mock('@/components/artifact/MarkdownPreview', () => ({
  default: ({ content }: { content: string }) => (
    <div data-testid="skill-markdown">{content}</div>
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('SkillPreviewPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    apiMock.getSkillDetail.mockReset();
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

  it('loads an admin catalog item through the admin detail channel', async () => {
    apiMock.getSkillDetail.mockResolvedValue({
      id: 'shared-1',
      slug: 'department-guide',
      name: 'Department guide',
      description: 'Department-only guidance',
      skill_md: '# Department guide\n\nFollow this.',
      source: 'dynamic',
      visibility: 'department',
      has_extra_files: true,
    });
    useUIStore.setState({
      artifactPanelVisible: true,
      skillRightView: { type: 'detail', skillId: 'shared-1', admin: true },
    });

    await act(async () => {
      root.render(<SkillPreviewPanel />);
    });

    expect(apiMock.getSkillDetail).toHaveBeenCalledWith('shared-1', { admin: true });
    const heading = container.querySelector('h2');
    expect(heading?.textContent).toBe('Department guide（department-guide）');
    expect(heading?.parentElement?.textContent).toContain('导入');
    expect(heading?.parentElement?.textContent).toContain('部门');
    const preview = container.querySelector('[data-testid="skill-markdown"]')?.textContent;
    expect(preview).toContain('### Description');
    expect(preview).toContain('Department-only guidance');
    expect(preview).toContain('`此技能包含附属文件');
    expect(preview).toContain('---');
    expect(preview).toContain('# Department guide\n\nFollow this.');
  });

  it('closes the shared right-panel visibility without changing the selection', async () => {
    apiMock.getSkillDetail.mockResolvedValue({
      id: 'private-1',
      slug: 'private-guide',
      name: 'Private guide',
      description: '',
      skill_md: 'Body',
      source: 'dynamic',
      visibility: 'private',
      has_extra_files: false,
    });
    useUIStore.setState({
      artifactPanelVisible: true,
      skillRightView: { type: 'detail', skillId: 'private-1', admin: false },
    });

    await act(async () => {
      root.render(<SkillPreviewPanel />);
    });
    const close = container.querySelector<HTMLButtonElement>(
      'button[aria-label="关闭技能说明"]',
    );
    await act(async () => close?.click());

    expect(apiMock.getSkillDetail).toHaveBeenCalledWith('private-1', { admin: false });
    expect(useUIStore.getState().artifactPanelVisible).toBe(false);
    expect(useUIStore.getState().skillRightView).toEqual({
      type: 'detail', skillId: 'private-1', admin: false,
    });
  });
});

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AdminArtifactInspector from './AdminArtifactInspector';

const apiMocks = vi.hoisted(() => ({
  listAdminConversationArtifacts: vi.fn(),
  getAdminConversationArtifact: vi.fn(),
  getAdminConversationArtifactVersion: vi.fn(),
  fetchAdminArtifactRawBlob: vi.fn(),
  fetchAdminArtifactRawObjectUrl: vi.fn(),
}));

vi.mock('@/lib/api', () => apiMocks);

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const summary = {
  id: 'artifact-1',
  session_id: 'conv-1',
  title: 'Quarterly report',
  content_type: 'text/markdown',
  current_version: 2,
  source: 'agent',
  original_filename: 'report.md',
  has_blob: false,
  created_at: '2026-08-10T01:02:03',
  updated_at: '2026-08-11T04:05:06',
};

const detail = {
  ...summary,
  content: '# Report',
  versions: [
    {
      version: 2,
      update_type: 'rewrite',
      created_at: '2026-08-11T04:05:06',
    },
  ],
};

describe('AdminArtifactInspector', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    apiMocks.listAdminConversationArtifacts.mockReset();
    apiMocks.listAdminConversationArtifacts.mockResolvedValue({
      session_id: 'conv-1',
      artifacts: [summary],
    });
    apiMocks.getAdminConversationArtifact.mockReset();
    apiMocks.getAdminConversationArtifact.mockResolvedValue(detail);
    apiMocks.getAdminConversationArtifactVersion.mockReset();
    apiMocks.fetchAdminArtifactRawBlob.mockReset();
    apiMocks.fetchAdminArtifactRawObjectUrl.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('moves admin artifact metadata out of the tree and into the full-width preview', async () => {
    await act(async () => {
      root.render(<AdminArtifactInspector conversationId="conv-1" refreshTick={0} />);
    });

    expect(container.querySelector('h3')?.textContent).toBe('会话文件');
    const fileRow = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('report.md'),
    );
    expect(fileRow).toBeDefined();
    expect(fileRow?.textContent).not.toContain('text/markdown');
    expect(fileRow?.textContent).not.toContain('v2');
    expect(fileRow?.getAttribute('title')).toBeNull();

    await act(async () => fileRow?.click());

    expect(container.querySelector('h3')?.textContent).toBe('report.md');
    expect(container.textContent).toContain('文件类型');
    expect(container.textContent).toContain('text/markdown');
    expect(container.textContent).toContain('创建时间');
    expect(container.textContent).toContain('最后更新');
    expect(container.textContent).toContain('Artifact ID');
    expect(container.textContent).toContain('artifact-1');
    expect(container.textContent).toContain('v2 · rewrite');
    expect(
      Array.from(container.querySelectorAll('div')).some((element) =>
        element.classList.contains('w-[280px]'),
      ),
    ).toBe(false);

    const backButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="返回文件列表"]',
    );
    await act(async () => backButton?.click());

    expect(container.querySelector('h3')?.textContent).toBe('会话文件');
    expect(container.textContent).not.toContain('Artifact ID');
  });

  it('does not offer previews or downloads for protected artifacts', async () => {
    apiMocks.listAdminConversationArtifacts.mockResolvedValue({
      session_id: 'conv-1',
      artifacts: [{
        ...summary,
        id: '__protected_artifact_1__',
        title: '受保护文件 1',
        source: 'agent',
        original_filename: null,
        content_accessible: false,
      }],
    });

    await act(async () => {
      root.render(<AdminArtifactInspector conversationId="conv-1" refreshTick={0} />);
    });

    expect(container.textContent).toContain('1 个会话文件受隐私保护');
    expect(container.textContent).toContain('没有可查看的会话文件');
    expect(container.querySelector('button[aria-label="下载文件"]')).toBeNull();
    expect(apiMocks.getAdminConversationArtifact).not.toHaveBeenCalled();
  });
});

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useStreamStore } from '@/stores/streamStore';
import PermissionModal from './PermissionModal';

const apiMocks = vi.hoisted(() => ({
  resumeExecution: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  resumeExecution: apiMocks.resumeExecution,
}));

vi.mock('./DialogShell', () => ({
  default: ({ children, footer }: { children: React.ReactNode; footer: React.ReactNode }) => (
    <div>{children}{footer}</div>
  ),
}));

vi.mock('@/components/markdown/InlineMarkdown', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe('PermissionModal', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    apiMocks.resumeExecution.mockReset();
    apiMocks.resumeExecution.mockResolvedValue({ stream_url: '/stream/msg-1' });
    useStreamStore.setState({
      conversationId: 'conv-1',
      messageId: 'msg-1',
      permissionRequest: {
        callId: 'call-sensitive-a',
        toolName: 'sensitive_tool',
        params: { target: 'A' },
      },
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useStreamStore.setState({
      conversationId: null,
      messageId: null,
      permissionRequest: null,
    });
  });

  it('submits the interrupted native call id with the authorization decision', async () => {
    await act(async () => {
      root.render(<PermissionModal />);
    });
    const alwaysAllow = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '始终允许',
    );
    expect(alwaysAllow).toBeDefined();

    await act(async () => {
      alwaysAllow?.click();
    });

    expect(apiMocks.resumeExecution).toHaveBeenCalledWith('conv-1', {
      message_id: 'msg-1',
      call_id: 'call-sensitive-a',
      approved: true,
      always_allow: true,
    });
  });

  it('does not clear a newer permission request when the prior response returns late', async () => {
    let resolveResponse!: (value: { stream_url: string }) => void;
    apiMocks.resumeExecution.mockReturnValue(new Promise((resolve) => {
      resolveResponse = resolve;
    }));
    await act(async () => {
      root.render(<PermissionModal />);
    });
    const allowOnce = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '允许一次',
    );

    await act(async () => {
      allowOnce?.click();
      await Promise.resolve();
    });
    act(() => {
      useStreamStore.getState().setPermissionRequest({
        callId: 'call-sensitive-b',
        toolName: 'other_sensitive_tool',
        params: { target: 'B' },
      });
    });
    await act(async () => {
      resolveResponse({ stream_url: '/stream/msg-1' });
      await Promise.resolve();
    });

    expect(useStreamStore.getState().permissionRequest?.callId)
      .toBe('call-sensitive-b');
  });
});

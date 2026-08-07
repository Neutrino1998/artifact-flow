import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MessageFeedbackDialog from './MessageFeedbackDialog';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('MessageFeedbackDialog', () => {
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

  it('shows positive tags and submits selected tags with optional detail', async () => {
    const onSubmit = vi.fn();
    await act(async () => {
      root.render(
        <MessageFeedbackDialog
          rating="positive"
          current={null}
          saving={false}
          error={null}
          onSubmit={onSubmit}
          onDelete={vi.fn()}
          onClose={vi.fn()}
        />,
      );
    });

    expect(document.body.textContent).toContain('解决了我的问题');
    expect(document.body.textContent).not.toContain('不正确或不完整');
    const tag = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('解决了我的问题'),
    );
    const textarea = document.body.querySelector('textarea');
    await act(async () => {
      tag?.click();
      if (textarea) {
        const setter = Object.getOwnPropertyDescriptor(
          HTMLTextAreaElement.prototype,
          'value',
        )?.set;
        setter?.call(textarea, '具体反馈');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    const submit = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === '提交',
    );
    await act(async () => submit?.click());

    expect(onSubmit).toHaveBeenCalledWith(['resolved_problem'], '具体反馈');
  });

  it('loads an existing negative feedback and exposes the revoke action', async () => {
    await act(async () => {
      root.render(
        <MessageFeedbackDialog
          rating="negative"
          current={{
            rating: 'negative',
            tags: ['lost_context'],
            detail: '遗漏了前文',
            created_at: '2026-08-07T00:00:00',
            updated_at: '2026-08-07T00:00:00',
          }}
          saving={false}
          error={null}
          onSubmit={vi.fn()}
          onDelete={vi.fn()}
          onClose={vi.fn()}
        />,
      );
    });

    expect(document.body.textContent).toContain('丢失上下文');
    expect(document.body.textContent).toContain('撤销反馈');
    expect(document.body.querySelector('textarea')?.value).toBe('遗漏了前文');
  });
});

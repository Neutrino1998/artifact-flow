import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import UserMessage from './UserMessage';

const { sendMessageMock } = vi.hoisted(() => ({
  sendMessageMock: vi.fn(async () => true),
}));

vi.mock('@/features/chat/runtime/useChat', () => ({
  useChat: () => ({ sendMessage: sendMessageMock }),
}));

describe('UserMessage context chips', () => {
  beforeEach(() => {
    sendMessageMock.mockClear();
  });

  it('renders uploads, references, and per-turn user-activated skills together', () => {
    const html = renderToStaticMarkup(
      <UserMessage
        content="review this"
        messageId="msg-1"
        parentId={null}
        pending
        attachments={[{ filename: 'brief.docx' }]}
        referencedArtifacts={[{ id: 'old-brief', filename: 'old-brief.docx' }]}
        activatedSkills={[{ slug: 'docx', name: 'Word 文档' }]}
      />,
    );

    expect(html).toContain('brief.docx');
    expect(html).toContain('old-brief.docx');
    expect(html).toContain('引用');
    expect(html).toContain('Word 文档');
    expect(html).toContain('review this');
  });

  it('uses reference chips as the visible content of a reference-only message', () => {
    const html = renderToStaticMarkup(
      <UserMessage
        content=""
        messageId="msg-ref"
        parentId={null}
        pending
        referencedArtifacts={[{ id: 'brief', filename: 'brief.docx' }]}
      />,
    );

    expect(html).toContain('brief.docx');
    expect(html).not.toContain('rounded-bubble');
  });

  it('uses skill chips as the visible content of an activation-only message', () => {
    const html = renderToStaticMarkup(
      <UserMessage
        content=""
        messageId="msg-2"
        parentId={null}
        pending
        activatedSkills={[{ slug: 'docx', name: 'Word 文档' }]}
      />,
    );

    expect(html).toContain('Word 文档');
    expect(html).not.toContain('rounded-bubble');
  });

  it('shows the persisted send time in the hover action bar', () => {
    const html = renderToStaticMarkup(
      <UserMessage
        content="hello"
        messageId="msg-3"
        parentId={null}
        timestamp="2026-08-07T08:30:00Z"
      />,
    );

    expect(html).toContain('发送时间：');
    expect(html).toContain('<time');
    expect(html.indexOf('<time')).toBeLessThan(html.indexOf('aria-label="Edit message"'));
  });

  it('does not show hover metadata while the message is pending', () => {
    const html = renderToStaticMarkup(
      <UserMessage
        content="hello"
        messageId=""
        parentId={null}
        pending
        timestamp="2026-08-07T08:30:00Z"
      />,
    );

    expect(html).not.toContain('发送时间：');
    expect(html).not.toContain('<time');
  });

  it('edits and reruns as text-only branches without replaying context chips', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <UserMessage
          content="review this"
          messageId="msg-replay"
          parentId="parent-1"
          attachments={[{ filename: 'new.docx' }]}
          referencedArtifacts={[{ id: 'old-doc', filename: 'old.docx' }]}
          activatedSkills={[{ slug: 'docx', name: 'Word 文档' }]}
        />,
      );
    });

    const rerun = container.querySelector<HTMLButtonElement>(
      '[aria-label="Rerun message"]',
    );
    expect(rerun).not.toBeNull();
    await act(async () => {
      rerun!.click();
    });
    expect(sendMessageMock.mock.calls[0]).toEqual(['review this', 'parent-1']);

    sendMessageMock.mockClear();
    const edit = container.querySelector<HTMLButtonElement>(
      '[aria-label="Edit message"]',
    );
    await act(async () => {
      edit!.click();
    });
    const sendEdit = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === '发送',
    );
    expect(sendEdit).toBeDefined();
    await act(async () => {
      sendEdit!.click();
    });
    expect(sendMessageMock.mock.calls[0]).toEqual(['review this', 'parent-1']);

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});

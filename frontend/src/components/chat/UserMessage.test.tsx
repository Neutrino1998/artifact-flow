import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import UserMessage from './UserMessage';

describe('UserMessage context chips', () => {
  it('renders attachments and per-turn user-activated skills together', () => {
    const html = renderToStaticMarkup(
      <UserMessage
        content="review this"
        messageId="msg-1"
        parentId={null}
        pending
        attachments={[{ filename: 'brief.docx' }]}
        activatedSkills={[{ slug: 'docx', name: 'Word 文档' }]}
      />,
    );

    expect(html).toContain('brief.docx');
    expect(html).toContain('Word 文档');
    expect(html).toContain('review this');
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
});

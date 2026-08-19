import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import SkillDisplayName from './SkillDisplayName';

describe('SkillDisplayName', () => {
  it('shows the model-facing slug when it differs from the display name', () => {
    const html = renderToStaticMarkup(
      <SkillDisplayName name="Word 文档" slug="docx" />,
    );

    expect(html).toContain('Word 文档');
    expect(html).toContain('（docx）');
  });

  it('does not repeat an identical name and slug', () => {
    const html = renderToStaticMarkup(
      <SkillDisplayName name="docx" slug="docx" />,
    );

    expect(html).toBe('docx');
  });
});

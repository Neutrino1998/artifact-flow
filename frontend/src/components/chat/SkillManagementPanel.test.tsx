import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SkillValidationNotices } from './SkillManagementPanel';

describe('SkillValidationNotices', () => {
  it('keeps non-blocking warnings out of the rejection notice', () => {
    const html = renderToStaticMarkup(
      <SkillValidationNotices
        findings={[
          {
            rule: 'md.body_empty',
            severity: 'error',
            message: 'SKILL.md body is empty',
          },
          {
            rule: 'fm.unknown_keys',
            severity: 'warning',
            message: 'unrecognized frontmatter keys',
          },
        ]}
      />
    );
    const document = new DOMParser().parseFromString(html, 'text/html');
    const errorNotice = document.querySelector('[role="alert"]');
    const warningNotice = document.querySelector('[role="status"]');

    expect(errorNotice?.textContent).toContain('SKILL.md body is empty');
    expect(errorNotice?.textContent).not.toContain('unrecognized frontmatter keys');
    expect(warningNotice?.textContent).toContain('unrecognized frontmatter keys');
    expect(warningNotice?.textContent).not.toContain('SKILL.md body is empty');
    expect(html).not.toContain('md.body_empty');
    expect(html).not.toContain('fm.unknown_keys');
  });
});

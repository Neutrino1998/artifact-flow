import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  mergeSkillRows,
  skillRowBorderClass,
  SkillValidationNotices,
} from './SkillManagementPanel';
import type { AdminSkillItem, SkillItem } from '@/types';

function skill(overrides: Partial<SkillItem> = {}): SkillItem {
  return {
    id: 'private-id',
    slug: 'review',
    name: 'Private review',
    description: 'private',
    enabled: true,
    default_enabled: true,
    is_overridden: false,
    source: 'dynamic',
    has_extra_files: false,
    visibility: 'private',
    is_owner: true,
    shadowed_by_private: false,
    ...overrides,
  };
}

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

describe('scoped skill rows', () => {
  it('keeps same-slug private and shared skills as separate rows by id', () => {
    const shared: AdminSkillItem = {
      id: 'shared-id',
      slug: 'review',
      name: 'Shared review',
      description: 'shared',
      visibility: 'public',
      default_enabled: true,
      source: 'dynamic',
      has_extra_files: false,
      can_edit: true,
    };
    const rows = mergeSkillRows([
      skill(),
      skill({
        id: 'shared-id',
        name: 'Shared review',
        visibility: 'public',
        is_owner: false,
        enabled: false,
        shadowed_by_private: true,
      }),
    ], [shared]);

    expect(rows).toHaveLength(2);
    expect(rows.find((row) => row.id === 'shared-id')?.adminShared).toEqual(shared);
  });

  it('uses an error border for a shared skill shadowed by private', () => {
    expect(skillRowBorderClass(skill({ shadowed_by_private: true })))
      .toContain('border-status-error');
  });
});

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import ComposerAutocomplete from './ComposerAutocomplete';

describe('ComposerAutocomplete', () => {
  it('renders the shared skill picker as a multi-select list', () => {
    const html = renderToStaticMarkup(
      <ComposerAutocomplete
        kind="skill"
        suggestions={[
          {
            key: 'skill:documents',
            title: 'Word 文档',
            description: '创建和编辑文档',
            badge: '已激活',
            selected: true,
          },
        ]}
        activeIndex={0}
        loading={false}
        error={false}
        hasConversation
        hint="可多选 · 输入 / 可搜索"
        multiSelect
        onActiveIndexChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    expect(html).toContain('aria-multiselectable="true"');
    expect(html).toContain('aria-selected="true"');
    expect(html).toContain('Word 文档');
    expect(html).toContain('创建和编辑文档');
    expect(html).toContain('已激活');
    expect(html).toContain('可多选 · 输入 / 可搜索');
    expect(html).toContain('overflow-hidden');
    expect(html).toContain('overflow-y-auto');
    expect(html).toContain('border-accent');
  });
});

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import MarkdownBlock from './MarkdownBlock';
import InlineMarkdown from './InlineMarkdown';

describe('Markdown math rendering', () => {
  it('renders inline and display math with KaTeX', () => {
    const html = renderToStaticMarkup(
      <MarkdownBlock>{'Inline $$E=mc^2$$.\n\n$$\na^2 + b^2 = c^2\n$$'}</MarkdownBlock>
    );

    expect(html).toContain('class="katex"');
    expect(html).toContain('class="katex-display"');
  });

  it('keeps ordinary dollar amounts as text', () => {
    const html = renderToStaticMarkup(
      <MarkdownBlock>{'Cost is $5 today and $10 tomorrow.'}</MarkdownBlock>
    );

    expect(html).not.toContain('class="katex"');
    expect(html).toContain('Cost is $5 today and $10 tomorrow.');
  });

  it('renders math in compact inline markdown', () => {
    const html = renderToStaticMarkup(<InlineMarkdown>{'Cost $$c(x)$$'}</InlineMarkdown>);

    expect(html).toContain('class="katex"');
  });
});

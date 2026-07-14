import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { StatusNotice } from './StatusNotice';

describe('StatusNotice', () => {
  it('renders a large cross without a second circle for errors', () => {
    const html = renderToStaticMarkup(
      <StatusNotice tone="error">导入失败</StatusNotice>
    );

    expect(html).toContain('role="alert"');
    expect(html).toContain('M3.5 3.5l9 9M12.5 3.5l-9 9');
    expect(html).not.toContain('<circle');
  });

  it('renders a large exclamation mark without an inner triangle for warnings', () => {
    const html = renderToStaticMarkup(
      <StatusNotice tone="warning">校验提示</StatusNotice>
    );

    expect(html).toContain('role="status"');
    expect(html).toContain('M8 2.25v5.75');
    expect(html).toContain('cx="8" cy="12.25" r="1.5"');
    expect(html).not.toContain('M8 2.5 14 13H2L8 2.5Z');
  });
});

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import StagedFileChip from './StagedFileChip';

describe('StagedFileChip', () => {
  it('uses the shared format icon for a non-image upload', () => {
    const file = new File(['brief'], 'brief.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    const html = renderToStaticMarkup(
      <StagedFileChip sf={{ id: 'staged-1', file }} onRemove={vi.fn()} />,
    );

    expect(html).toContain('tabler-icon-file-word');
    expect(html).toContain('brief.docx');
  });
});

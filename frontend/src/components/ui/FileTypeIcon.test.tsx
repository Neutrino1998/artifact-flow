import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FileTypeIcon, fileTypeLabel } from './FileTypeIcon';

describe('FileTypeIcon', () => {
  it('uses MIME when available and filename extensions for display-only snapshots', () => {
    expect(fileTypeLabel('application/pdf')).toBe('PDF');
    expect(fileTypeLabel(undefined, 'brief.docx')).toBe('DOCX');
    expect(fileTypeLabel(undefined, 'config.json')).toBe('JSON');
  });

  it('renders semantic icons from filename-only attachment data', () => {
    const docx = renderToStaticMarkup(<FileTypeIcon filename="brief.docx" />);
    const json = renderToStaticMarkup(<FileTypeIcon filename="config.json" />);
    const image = renderToStaticMarkup(<FileTypeIcon filename="preview.webp" />);
    const unknown = renderToStaticMarkup(<FileTypeIcon filename="payload.unknown" />);

    expect(docx).toContain('tabler-icon-file-word');
    expect(json).toContain('tabler-icon-file-code');
    expect(image).toContain('tabler-icon-photo');
    expect(unknown).toContain('tabler-icon-file');
  });
});

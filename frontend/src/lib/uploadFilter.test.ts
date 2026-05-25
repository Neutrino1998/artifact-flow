import { describe, test, expect } from 'vitest';
import { rejectionReason, partitionStageable } from './uploadFilter';

describe('uploadFilter.rejectionReason', () => {
  test('rejects blacklisted office extensions with backend-matching wording', () => {
    // Must match src/utils/doc_converter.py's message verbatim.
    expect(rejectionReason('report.doc')).toBe(
      '暂不支持 .doc 格式（Word 文件）。请用 Office/WPS 另存为 .docx 后再上传。',
    );
    expect(rejectionReason('budget.xlsx')).toBe(
      '暂不支持 .xlsx 格式（Excel 文件）。请导出为 .csv，或将需要的内容复制到对话框。',
    );
    expect(rejectionReason('macro.docm')).toContain('取消宏');
    expect(rejectionReason('slides.pptm')).toContain('PowerPoint');
    expect(rejectionReason('sheet.ods')).toContain('ODF 表格');
  });

  test('accepts supported / unknown extensions (backend attempts them as text)', () => {
    expect(rejectionReason('notes.txt')).toBeNull();
    expect(rejectionReason('doc.docx')).toBeNull();
    expect(rejectionReason('paper.pdf')).toBeNull();
    expect(rejectionReason('code.py')).toBeNull();
    expect(rejectionReason('readme.md')).toBeNull();
    expect(rejectionReason('weird.xyz')).toBeNull();
    expect(rejectionReason('noextension')).toBeNull();
  });

  test('extension match is case-insensitive', () => {
    expect(rejectionReason('A.DOC')).not.toBeNull();
    expect(rejectionReason('B.XlSx')).not.toBeNull();
  });

  test('leading-dot names are dotfiles with no extension (matches os.path.splitext)', () => {
    // Backend: splitext('.doc') == ('.doc', '') → attempted as text. We must
    // accept these, not reject them, to honor "only block what the backend rejects".
    expect(rejectionReason('.doc')).toBeNull();
    expect(rejectionReason('..doc')).toBeNull();
    expect(rejectionReason('.xlsx')).toBeNull();
    // ...but a real extension after a non-dot char still rejects.
    expect(rejectionReason('a..doc')).not.toBeNull(); // splitext → '.doc'
    expect(rejectionReason('archive.tar.doc')).not.toBeNull();
  });
});

describe('uploadFilter.partitionStageable', () => {
  test('splits accepted vs rejected, preserving order within each', () => {
    const { accepted, rejected } = partitionStageable([
      new File(['x'], 'a.txt'),
      new File(['x'], 'b.doc'),
      new File(['x'], 'c.md'),
      new File(['x'], 'd.ods'),
    ]);
    expect(accepted.map((f) => f.name)).toEqual(['a.txt', 'c.md']);
    expect(rejected.map((r) => r.name)).toEqual(['b.doc', 'd.ods']);
  });
});

// Client-side mirror of the backend's upload format classification. The backend
// (src/utils/doc_converter.py `DocConverter.convert`) rejects a fixed set of
// Office/ODF extensions on sight with a 422 — gating them here keeps a doomed
// file out of the staged batch, avoiding a wasted round-trip and the
// partial-batch state it could leave behind.
//
// KEEP IN SYNC with src/utils/doc_converter.py `_UNSUPPORTED_OFFICE`: that file
// is the source of truth. The backend is otherwise PERMISSIVE — anything not in
// this blacklist (and not .docx/.pdf) is attempted as text — so we must NOT
// whitelist here, or we'd block files the backend would happily accept. Only
// mirror the blacklist. Content-level failures the extension can't predict
// (a corrupt .pdf, an undecodable "text" file) still surface as a backend 422;
// this gate only catches the cases knowable from the extension alone.

type Category = readonly [label: string, advice: string];

const WORD: Category = ['Word', '请用 Office/WPS 另存为 .docx 后再上传'];
const WORD_MACRO: Category = ['Word', '请用 Office/WPS 另存为 .docx（取消宏）后再上传'];
const EXCEL: Category = ['Excel', '请导出为 .csv，或将需要的内容复制到对话框'];
const PPT: Category = ['PowerPoint', '请导出为 PDF（文字版），或将需要的内容复制到对话框'];
const ODF_TEXT: Category = ['ODF 文档', '请另存为 .docx 或 .pdf 后再上传'];
const ODF_CALC: Category = ['ODF 表格', '请导出为 .csv，或将需要的内容复制到对话框'];
const ODF_IMPRESS: Category = ['ODF 演示', '请导出为 PDF（文字版），或将需要的内容复制到对话框'];

const UNSUPPORTED_OFFICE: Record<string, Category> = {
  // Word（老二进制 + 模板 + 宏）
  '.doc': WORD,
  '.docm': WORD_MACRO,
  '.docb': WORD,
  '.dot': WORD,
  '.dotx': WORD,
  '.dotm': WORD_MACRO,
  // Excel（老二进制 + 现代 OOXML + 模板 + 宏 + 二进制工作簿）
  '.xls': EXCEL,
  '.xlsx': EXCEL,
  '.xlsm': EXCEL,
  '.xlsb': EXCEL,
  '.xlt': EXCEL,
  '.xltx': EXCEL,
  '.xltm': EXCEL,
  // PowerPoint（老二进制 + 现代 + 模板 + 宏 + 自动播放）
  '.ppt': PPT,
  '.pptx': PPT,
  '.pptm': PPT,
  '.pps': PPT,
  '.ppsx': PPT,
  '.ppsm': PPT,
  '.pot': PPT,
  '.potx': PPT,
  '.potm': PPT,
  // LibreOffice / ODF
  '.odt': ODF_TEXT,
  '.ott': ODF_TEXT,
  '.ods': ODF_CALC,
  '.ots': ODF_CALC,
  '.odp': ODF_IMPRESS,
  '.otp': ODF_IMPRESS,
};

export interface StageRejection {
  name: string;
  reason: string;
}

function extOf(filename: string): string {
  // Mirror Python os.path.splitext: leading dots are part of the name, not an
  // extension separator — ".doc", "..doc", ".gitignore" have NO extension, so
  // the backend attempts them as text. Only treat the last dot as an extension
  // boundary if at least one non-dot char precedes it in the name. (File.name
  // is a basename — browsers strip any path — so no separator handling needed.)
  const lastDot = filename.lastIndexOf('.');
  if (lastDot <= 0) return '';
  for (let i = 0; i < lastDot; i++) {
    if (filename[i] !== '.') return filename.slice(lastDot).toLowerCase();
  }
  return '';
}

/**
 * If `filename`'s extension is on the backend's unsupported-Office blacklist,
 * return the user-facing rejection message (mirrors doc_converter.py's wording);
 * otherwise null (the backend would attempt it).
 */
export function rejectionReason(filename: string): string | null {
  const cat = UNSUPPORTED_OFFICE[extOf(filename)];
  if (!cat) return null;
  const [label, advice] = cat;
  return `暂不支持 ${extOf(filename)} 格式（${label} 文件）。${advice}。`;
}

/** Split files into those the backend would accept (by extension) and those it
 *  rejects on sight, with a per-file reason for the rejected ones. */
export function partitionStageable(files: File[]): {
  accepted: File[];
  rejected: StageRejection[];
} {
  const accepted: File[] = [];
  const rejected: StageRejection[] = [];
  for (const file of files) {
    const reason = rejectionReason(file.name);
    if (reason) rejected.push({ name: file.name, reason });
    else accepted.push(file);
  }
  return { accepted, rejected };
}

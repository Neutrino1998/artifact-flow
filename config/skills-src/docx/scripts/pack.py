#!/usr/bin/env python3
"""把 unpack.py 解开的目录打回 .docx,并做两级完整性检查。

用法:
    python pack.py 解开的目录/ 输出.docx

检查(打包前后各一级,坏 XML 当场报文件名+行号,不让它活到 Word 打不开):
  1. 打包前:每个 *.xml / *.rels 过一遍 XML 解析(语法级,报错带行列号);
  2. 打包后:python-docx 试开(结构级,document.xml 缺失/关系断裂等)。

unpack 的断行(元素间空白)无需还原 —— OOXML 对元素间空白不敏感。
"""

import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    if not (src_dir / "[Content_Types].xml").is_file():
        raise SystemExit(f"error: {src_dir} 不是解开的 docx(缺 [Content_Types].xml)")

    files = sorted(p for p in src_dir.rglob("*") if p.is_file())

    # 级 1:XML 语法
    bad = []
    for p in files:
        if p.suffix in (".xml", ".rels"):
            try:
                ET.fromstring(p.read_bytes())
            except ET.ParseError as e:
                bad.append(f"  {p.relative_to(src_dir)}: {e}")
    if bad:
        raise SystemExit("XML 语法错误,已中止打包:\n" + "\n".join(bad))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(src_dir).as_posix())

    # 级 2:python-docx 结构级试开
    try:
        import docx  # noqa: PLC0415

        docx.Document(str(out))
    except Exception as e:  # noqa: BLE001 — 任何打不开都要拦
        raise SystemExit(f"打包完成但 python-docx 无法打开(结构损坏?): {e}")

    print(f"packed {out} ({out.stat().st_size:,} bytes), 完整性检查通过")


if __name__ == "__main__":
    main()

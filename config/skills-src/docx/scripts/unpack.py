#!/usr/bin/env python3
"""解开 .docx(OOXML zip)到目录,并把 XML 断行成可读可编辑形态。

用法:
    python unpack.py 输入.docx 输出目录/

断行策略:只把字节序列 `><` 换成 `>\n<`。`<`/`>` 在文本节点里必然被转义
(&lt;/&gt;),所以 `><` 只可能出现在相邻标签之间 —— 该替换永远不会污染
<w:t> 的文本内容,只引入元素间空白(OOXML 语义上不敏感)。这比 XML 库
pretty-print 安全:不重排命名空间前缀(mc:Ignorable 引用前缀名,重命名会破坏),
不动 xml:space="preserve" 的文本。

打包回去用同目录的 pack.py(原样压缩,断行无需还原)。
"""

import sys
import zipfile
from pathlib import Path

_XML_SUFFIXES = (".xml", ".rels")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            name = info.filename
            # 防路径逃逸:zip 条目不得指向输出目录之外
            if name.startswith("/") or ".." in name.split("/"):
                raise SystemExit(f"unsafe zip member: {name!r}")
            if info.is_dir():
                continue
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(name)
            if name.endswith(_XML_SUFFIXES):
                data = data.replace(b"><", b">\n<")
            target.write_bytes(data)
            print(name)
    print(f"\nunpacked to {out_dir}/ — edit XML, then repack with pack.py")


if __name__ == "__main__":
    main()

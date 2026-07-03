#!/usr/bin/env python3
"""PDF 页面渲染成 PNG —— 扫描件/纯图 PDF 交给 vision_agent 的前置步骤。

用法:
    python pdf_to_images.py 输入.pdf 输出目录/ [--pages 1-5,8] [--dpi 150]

产出 输出目录/page_0001.png …(页码从 1 计)。默认渲染前 20 页 —— 更长的文档
分批渲染分批委派,不要一次转几百页把配额打爆。渲染后把需要识别的页
`persist` 成 artifact,再委派 vision_agent。
"""

import argparse
from pathlib import Path

import pypdfium2 as pdfium

MAX_PAGES_PER_RUN = 20   # 显式 --pages 也不越过:单次渲染的算法上界,长文档分批
MAX_DPI = 300            # vision 识别 300dpi 足够,封顶防超采样烧时间


def _parse_pages(expr, total):
    """"1-5,8" → [0,1,2,3,4,7](内部 0 基);None → 前 MAX_PAGES_PER_RUN 页。"""
    if not expr:
        return list(range(min(total, MAX_PAGES_PER_RUN)))
    out = []
    for part in expr.split(","):
        part = part.strip()
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a), int(b)
                if lo > hi:
                    raise ValueError
                out.extend(range(lo - 1, min(hi, total)))
            else:
                out.append(int(part) - 1)
        except ValueError:
            raise SystemExit(
                f'error: --pages 片段无法解析: {part!r}(格式如 "1-5,8",1 基页码)')
    pages = sorted({p for p in out if 0 <= p < total})
    if len(pages) > MAX_PAGES_PER_RUN:
        print(f"note: 请求 {len(pages)} 页,单次上限 {MAX_PAGES_PER_RUN} 页,"
              f"只渲染其中前 {MAX_PAGES_PER_RUN} 张 —— 其余分批")
        pages = pages[:MAX_PAGES_PER_RUN]
    return pages


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("out_dir")
    ap.add_argument("--pages", default=None, help='如 "1-5,8"(1 基页码)')
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    if args.dpi <= 0:
        raise SystemExit("error: --dpi 必须为正整数")
    dpi = min(args.dpi, MAX_DPI)
    if dpi != args.dpi:
        print(f"note: --dpi {args.dpi} 超上限,按 {MAX_DPI} 渲染")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(args.src)
    total = len(doc)
    pages = _parse_pages(args.pages, total)
    if not args.pages and total > MAX_PAGES_PER_RUN:
        print(f"note: 文档共 {total} 页,本次只渲染前 {MAX_PAGES_PER_RUN} 页;"
              f"其余用 --pages 指定分批处理")

    for idx in pages:
        page = doc[idx]
        bitmap = page.render(scale=dpi / 72)
        img = bitmap.to_pil()
        target = out / f"page_{idx + 1:04d}.png"
        img.save(target)
        print(f"{target}  ({img.width}x{img.height})")
        bitmap.close()
        page.close()
    doc.close()
    print(f"rendered {len(pages)}/{total} pages at {dpi} dpi")


if __name__ == "__main__":
    main()

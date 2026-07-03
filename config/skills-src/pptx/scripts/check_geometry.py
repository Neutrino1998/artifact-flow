#!/usr/bin/env python3
"""演示文稿静态质检 —— 无渲染环境下的几何与内容检查,生成后必跑。

用法:
    python check_geometry.py 文件.pptx

检查项(输出 JSON,issues 非空 → 退出码 1):
  out_of_bounds   形状越出画布
  overlap         两个含文字的形状明显重叠(相交面积 > 较小者的 30%)
  text_overload   单个文本框行数过多(>8 段)或单页字符过密(>600 字)
  placeholder     占位符残留(Click to add / Lorem / TODO / XXX / 占位)
  tiny_font       字号 < 12pt 的正文文字
  empty_slide     整页没有任何可见内容

这是启发式而非渲染验证 —— 报出的项要人工确认,没报不等于完美。
"""

import json
import re
import sys

from pptx import Presentation
from pptx.util import Emu

_PLACEHOLDER_RE = re.compile(r"click to add|lorem|ipsum|TODO|XXX+|占位|待填", re.I)


def _box(shape):
    try:
        return (shape.left, shape.top, shape.left + shape.width, shape.top + shape.height)
    except (TypeError, ValueError):
        return None


def _shape_text(shape):
    if not getattr(shape, "has_text_frame", False):
        return ""
    return "\n".join(p.text for p in shape.text_frame.paragraphs).strip()


def _overlap_area(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    prs = Presentation(sys.argv[1])
    page = (0, 0, prs.slide_width, prs.slide_height)
    issues = []

    for idx, slide in enumerate(prs.slides, start=1):
        texted = []   # (box, text) 仅含文字的形状参与重叠判定
        total_chars = 0
        any_content = False

        for shape in slide.shapes:
            box = _box(shape)
            text = _shape_text(shape)
            if text:
                any_content = True
            else:
                try:
                    any_content = any_content or shape.image is not None
                except (AttributeError, ValueError):
                    pass  # 非图片形状(纯色块不算内容)

            if box is not None and (
                box[0] < -Emu(9144) or box[1] < -Emu(9144)
                or box[2] > page[2] + Emu(9144) or box[3] > page[3] + Emu(9144)
            ):
                issues.append({"slide": idx, "kind": "out_of_bounds",
                               "detail": (text[:30] or str(shape.shape_type))})

            if not text:
                continue
            total_chars += len(text)
            if _PLACEHOLDER_RE.search(text):
                issues.append({"slide": idx, "kind": "placeholder", "detail": text[:50]})
            n_paras = len([p for p in shape.text_frame.paragraphs if p.text.strip()])
            if n_paras > 8:
                issues.append({"slide": idx, "kind": "text_overload",
                               "detail": f"{n_paras} 段落于单个文本框"})
            for p in shape.text_frame.paragraphs:
                for run in p.runs:
                    if run.font.size is not None and run.font.size.pt < 12 and run.text.strip():
                        issues.append({"slide": idx, "kind": "tiny_font",
                                       "detail": f"{run.font.size.pt:.0f}pt: {run.text[:20]}"})
            if box is not None:
                texted.append((box, text))

        if total_chars > 600:
            issues.append({"slide": idx, "kind": "text_overload",
                           "detail": f"整页 {total_chars} 字符"})
        if not any_content:
            issues.append({"slide": idx, "kind": "empty_slide", "detail": ""})

        for i in range(len(texted)):
            for j in range(i + 1, len(texted)):
                (ba, ta), (bb, tb) = texted[i], texted[j]
                inter = _overlap_area(ba, bb)
                smaller = min((ba[2] - ba[0]) * (ba[3] - ba[1]),
                              (bb[2] - bb[0]) * (bb[3] - bb[1]))
                if smaller > 0 and inter / smaller > 0.30:
                    issues.append({"slide": idx, "kind": "overlap",
                                   "detail": f"{ta[:20]!r} × {tb[:20]!r}"})

    # 去重(同页同类同 detail)
    seen, uniq = set(), []
    for it in issues:
        key = (it["slide"], it["kind"], it["detail"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)

    print(json.dumps({"slides": len(list(prs.slides)), "issues": uniq},
                     ensure_ascii=False, indent=1))
    sys.exit(1 if uniq else 0)


if __name__ == "__main__":
    main()

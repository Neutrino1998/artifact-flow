#!/usr/bin/env python3
"""从内容 JSON 生成 16:9 演示文稿 —— 布局与配色固化在脚本里,保证整份 deck 一致。

用法:
    python build_deck.py deck.json 输出.pptx

deck.json 结构(theme 用主题名,或内联 token 覆盖):
{
  "theme": "曜石蓝",
  "slides": [
    {"layout": "cover",   "title": "...", "subtitle": "...", "footer": "..."},
    {"layout": "section", "number": "01", "title": "..."},
    {"layout": "bullets", "title": "...", "bullets": ["要点", {"text": "子项", "level": 1}]},
    {"layout": "two_col", "title": "...", "left_title": "...", "left": [...],
                          "right_title": "...", "right": [...]},
    {"layout": "table",   "title": "...", "columns": ["列1"], "rows": [["值"]]},
    {"layout": "image",   "title": "...", "image": "chart.png", "caption": "..."},
    {"layout": "closing", "title": "谢谢", "subtitle": "..."}
  ]
}

主题名与 assets/design_gallery.html 样单一致。自定义配色传对象:
  "theme": {"primary": "1B2A4A", "accent": "C9A227", "bg": "FFFFFF",
            "text": "2B2B2B", "muted": "6B7280", "light": "EEF1F6"}

生成后用 check_geometry.py 做静态检查。
"""

import json
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

# 16:9 画布(英寸)
PAGE_W, PAGE_H = 13.333, 7.5
MARGIN = 0.83  # 统一页边距

# 主题 token:primary 承担 60-70% 的色彩存在感,accent 只点睛(条/数字/强调),
# 绝不大面积使用 —— 单页超过两种彩色就已经太多。
THEMES = {
    "曜石蓝": {"primary": "1B2A4A", "accent": "C9A227", "bg": "FFFFFF",
               "text": "252A33", "muted": "6B7280", "light": "EEF1F6"},
    "赭墨":   {"primary": "9C7050", "accent": "3E3A36", "bg": "FAF6F0",
               "text": "2B2B2B", "muted": "8A8378", "light": "F0E7DC"},
    "松石":   {"primary": "0F6B5C", "accent": "D97706", "bg": "FFFFFF",
               "text": "1F2937", "muted": "6B7280", "light": "E8F2EF"},
    "绛红":   {"primary": "8C2F39", "accent": "B08D57", "bg": "FFFFFF",
               "text": "2B2B2B", "muted": "757575", "light": "F5ECEC"},
    "极简":   {"primary": "333333", "accent": "2563EB", "bg": "FFFFFF",
               "text": "333333", "muted": "9CA3AF", "light": "F3F4F6"},
    "晨橙":   {"primary": "C2570B", "accent": "334155", "bg": "FFFDF9",
               "text": "292524", "muted": "78716C", "light": "FDEBD9"},
}
FONT = "微软雅黑"  # 字体名只是文件里的引用串,渲染发生在用户机器上


def _c(hexstr):
    return RGBColor.from_string(hexstr)


def _style_run(run, *, size, color, bold=False, font=FONT):
    f = run.font
    f.size, f.bold, f.name = Pt(size), bold, font
    f.color.rgb = _c(color)
    # python-pptx 的 font.name 只设拉丁字形;中文走 a:ea,必须补一笔
    rpr = run._r.get_or_add_rPr()
    ea = rpr.find(qn("a:ea"))
    if ea is None:
        ea = rpr.makeelement(qn("a:ea"), {})
        rpr.append(ea)
    ea.set("typeface", font)


def _textbox(slide, x, y, w, h):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.text_frame.word_wrap = True
    return box


def _put_text(tf, lines, *, size, color, bold=False, leading=1.15,
              space_after=6, align=PP_ALIGN.LEFT):
    """lines: [str | {"text":…, "level":0/1}];首段复用 tf.paragraphs[0]。"""
    first = True
    for item in lines:
        text, level = (item, 0) if isinstance(item, str) else (item["text"], item.get("level", 0))
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.level = level
        p.line_spacing = leading
        p.space_after = Pt(space_after)
        run = p.add_run()
        run.text = text
        lv_size = size if level == 0 else size - 2
        _style_run(run, size=lv_size, color=color, bold=bold)
    return tf


def _rect(slide, x, y, w, h, color):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = _c(color)
    shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _title_bar(slide, t, title):
    """内容页统一题头:accent 短条 + 标题 —— 全篇同一视觉锚点。"""
    _rect(slide, MARGIN, 0.62, 0.55, 0.09, t["accent"])
    box = _textbox(slide, MARGIN, 0.78, PAGE_W - 2 * MARGIN, 0.9)
    _put_text(box.text_frame, [title], size=28, color=t["primary"], bold=True)


def slide_cover(prs, t, spec):
    s = _blank(prs)
    _rect(s, 0, 0, PAGE_W, PAGE_H, t["primary"])
    _rect(s, MARGIN, 3.02, 1.2, 0.12, t["accent"])
    box = _textbox(s, MARGIN, 3.3, PAGE_W - 2 * MARGIN, 1.6)
    _put_text(box.text_frame, [spec["title"]], size=40, color="FFFFFF", bold=True)
    if spec.get("subtitle"):
        box = _textbox(s, MARGIN, 4.75, PAGE_W - 2 * MARGIN, 0.8)
        _put_text(box.text_frame, [spec["subtitle"]], size=18, color="D9DEE8")
    if spec.get("footer"):
        box = _textbox(s, MARGIN, 6.7, PAGE_W - 2 * MARGIN, 0.4)
        _put_text(box.text_frame, [spec["footer"]], size=12, color="9AA3B5")


def slide_section(prs, t, spec):
    s = _blank(prs)
    _rect(s, 0, 0, PAGE_W, PAGE_H, t["light"])
    _rect(s, 0, 0, 0.18, PAGE_H, t["primary"])
    if spec.get("number"):
        box = _textbox(s, MARGIN, 2.2, 3.0, 1.6)
        _put_text(box.text_frame, [spec["number"]], size=66, color=t["accent"], bold=True)
    box = _textbox(s, MARGIN, 3.7, PAGE_W - 2 * MARGIN, 1.4)
    _put_text(box.text_frame, [spec["title"]], size=34, color=t["primary"], bold=True)


def slide_bullets(prs, t, spec):
    s = _blank(prs)
    _title_bar(s, t, spec["title"])
    box = _textbox(s, MARGIN, 1.95, PAGE_W - 2 * MARGIN, PAGE_H - 2.6)
    _put_text(box.text_frame, spec["bullets"], size=18, color=t["text"],
              leading=1.3, space_after=12)


def slide_two_col(prs, t, spec):
    s = _blank(prs)
    _title_bar(s, t, spec["title"])
    col_w = (PAGE_W - 2 * MARGIN - 0.6) / 2
    for i, side in enumerate(("left", "right")):
        x = MARGIN + i * (col_w + 0.6)
        _rect(s, x, 1.95, col_w, 0.02, t["light"])
        if spec.get(f"{side}_title"):
            box = _textbox(s, x, 2.1, col_w, 0.55)
            _put_text(box.text_frame, [spec[f"{side}_title"]], size=17,
                      color=t["accent"], bold=True)
        box = _textbox(s, x, 2.75, col_w, PAGE_H - 3.4)
        _put_text(box.text_frame, spec.get(side, []), size=15, color=t["text"],
                  leading=1.3, space_after=8)


def slide_table(prs, t, spec):
    s = _blank(prs)
    _title_bar(s, t, spec["title"])
    cols, rows = spec["columns"], spec["rows"]
    shape = s.shapes.add_table(
        len(rows) + 1, len(cols),
        Inches(MARGIN), Inches(2.0),
        Inches(PAGE_W - 2 * MARGIN), Inches(min(0.5 * (len(rows) + 1), PAGE_H - 2.7)),
    )
    table = shape.table
    for j, name in enumerate(cols):
        cell = table.cell(0, j)
        cell.text = str(name)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _c(t["primary"])
        for p in cell.text_frame.paragraphs:
            for run in p.runs:
                _style_run(run, size=14, color="FFFFFF", bold=True)
    for i, row in enumerate(rows, start=1):
        for j, val in enumerate(row):
            cell = table.cell(i, j)
            cell.text = str(val)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _c(t["bg"] if i % 2 else t["light"])
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    _style_run(run, size=13, color=t["text"])


def slide_image(prs, t, spec):
    s = _blank(prs)
    _title_bar(s, t, spec["title"])
    # 图片等比放进内容区盒子(以宽度为准,超高再按高度缩)
    box_x, box_y = MARGIN, 2.0
    box_w, box_h = PAGE_W - 2 * MARGIN, PAGE_H - 2.0 - (0.75 if spec.get("caption") else 0.4)
    pic = s.shapes.add_picture(spec["image"], Inches(box_x), Inches(box_y),
                               width=Inches(box_w))
    if pic.height > Inches(box_h):
        ratio = Inches(box_h) / pic.height
        pic.height, pic.width = Inches(box_h), Emu(int(pic.width * ratio))
    pic.left = Emu(int((Inches(PAGE_W) - pic.width) / 2))
    if spec.get("caption"):
        cap = _textbox(s, MARGIN, PAGE_H - 0.72, PAGE_W - 2 * MARGIN, 0.45)
        _put_text(cap.text_frame, [spec["caption"]], size=12, color=t["muted"],
                  align=PP_ALIGN.CENTER)


def slide_closing(prs, t, spec):
    s = _blank(prs)
    _rect(s, 0, 0, PAGE_W, PAGE_H, t["primary"])
    box = _textbox(s, MARGIN, 3.1, PAGE_W - 2 * MARGIN, 1.2)
    _put_text(box.text_frame, [spec.get("title", "谢谢")], size=40, color="FFFFFF",
              bold=True, align=PP_ALIGN.CENTER)
    if spec.get("subtitle"):
        box = _textbox(s, MARGIN, 4.4, PAGE_W - 2 * MARGIN, 0.7)
        _put_text(box.text_frame, [spec["subtitle"]], size=16, color="D9DEE8",
                  align=PP_ALIGN.CENTER)


LAYOUTS = {
    "cover": slide_cover, "section": slide_section, "bullets": slide_bullets,
    "two_col": slide_two_col, "table": slide_table, "image": slide_image,
    "closing": slide_closing,
}


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())

    theme = spec.get("theme", "曜石蓝")
    if isinstance(theme, str):
        if theme not in THEMES:
            raise SystemExit(f"未知主题 {theme!r},可选: {'、'.join(THEMES)},或传 token 对象")
        t = THEMES[theme]
    else:
        t = {**THEMES["曜石蓝"], **theme}

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(PAGE_W), Inches(PAGE_H)

    for i, slide_spec in enumerate(spec["slides"]):
        layout = slide_spec.get("layout")
        if layout not in LAYOUTS:
            raise SystemExit(f"slides[{i}]: 未知 layout {layout!r},可选: {'、'.join(LAYOUTS)}")
        LAYOUTS[layout](prs, t, slide_spec)

    prs.save(sys.argv[2])
    print(f"built {sys.argv[2]}: {len(spec['slides'])} slides, theme={theme if isinstance(theme, str) else 'custom'}")


if __name__ == "__main__":
    main()

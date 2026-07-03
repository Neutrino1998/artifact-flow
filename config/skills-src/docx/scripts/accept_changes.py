#!/usr/bin/env python3
"""接受/拒绝 .docx 修订(track changes)—— 纯 Python,不依赖 Word/LibreOffice。

用法:
    python accept_changes.py 输入.docx 输出.docx --accept            # 接受全部
    python accept_changes.py 输入.docx 输出.docx --reject            # 拒绝全部
    python accept_changes.py 输入.docx 输出.docx --accept --author 张三   # 只处理某作者

处理范围:document.xml + header/footer + footnotes/endnotes 中的
  插入/删除(w:ins/w:del)、移动(w:moveFrom/w:moveTo)、格式修订(w:rPrChange 等)、
  段落标记增删(合并段落)、表格行增删。
已知不处理(遇到会在 summary 的 skipped 里点名):单元格级 w:cellIns/w:cellDel/
w:cellMerge(接受删除涉及表格重排)。处理完建议用 check_redlines.py 或
`pandoc --track-changes=all` 复核结果。

输出末行是一个 JSON summary,机器可读。
"""

import argparse
import json
import zipfile
from fnmatch import fnmatch

from lxml import etree

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % NS_W

# 修订内容可能出现的部件(headers/footers 编号不定,用通配)
_PART_PATTERNS = [
    "word/document.xml", "word/header*.xml", "word/footer*.xml",
    "word/footnotes.xml", "word/endnotes.xml",
]

# 格式修订标记:元素内嵌一份「旧格式」,accept=删标记,reject=回滚到旧格式
_PROP_CHANGE = {
    W + "rPrChange": W + "rPr",
    W + "pPrChange": W + "pPr",
    W + "sectPrChange": W + "sectPr",
    W + "tblPrChange": W + "tblPr",
    W + "trPrChange": W + "trPr",
    W + "tcPrChange": W + "tcPr",
}
_RANGE_MARKERS = {
    W + "moveFromRangeStart", W + "moveFromRangeEnd",
    W + "moveToRangeStart", W + "moveToRangeEnd",
}
_CELL_MARKERS = {W + "cellIns", W + "cellDel", W + "cellMerge"}


def _matches_author(el, author):
    return author is None or el.get(W + "author") == author


def _is_para_mark(el):
    """w:pPr/w:rPr 里的空 w:ins / w:del = 段落标记修订,不是内容修订。"""
    parent = el.getparent()
    return (
        parent is not None and parent.tag == W + "rPr"
        and parent.getparent() is not None and parent.getparent().tag == W + "pPr"
    )


def _unwrap(el):
    """把 el 的子元素提升到 el 的位置,删除 el 本身(保 tail)。"""
    parent = el.getparent()
    idx = parent.index(el)
    children = list(el)
    for child in reversed(children):
        parent.insert(idx, child)
    if el.tail:
        if children:
            children[-1].tail = (children[-1].tail or "") + el.tail
        elif idx > 0:
            prev = parent[idx - 1 + len(children)]
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _remove(el):
    parent = el.getparent()
    if el.tail:
        idx = parent.index(el)
        if idx > 0:
            parent[idx - 1].tail = (parent[idx - 1].tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _rename_deltext(el):
    """reject 还原删除内容:w:delText → w:t,w:delInstrText → w:instrText。"""
    for node in el.iter(W + "delText"):
        node.tag = W + "t"
    for node in el.iter(W + "delInstrText"):
        node.tag = W + "instrText"


def _merge_with_next_para(p, stats):
    """段落标记消失 → 本段与下一段合并(保留下一段的段落属性,符合 Word 语义)。"""
    parent = p.getparent()
    nxt = p.getnext()
    if nxt is None or nxt.tag != W + "p":
        # 末段/邻接表格:段落标记留着比错误重排安全
        stats["skipped"].append("paragraph-mark change at boundary (kept as-is)")
        return
    insert_at = 1 if (len(nxt) and nxt[0].tag == W + "pPr") else 0
    for child in reversed([c for c in p if c.tag != W + "pPr"]):
        nxt.insert(insert_at, child)
    parent.remove(p)
    stats["paragraph_merges"] += 1


def _row_of(el):
    node = el
    while node is not None and node.tag != W + "tr":
        node = node.getparent()
    return node


def process_tree(root, mode, author, stats):
    accept = mode == "accept"
    # 反文档序遍历:子元素先于父元素处理,嵌套修订(如 w:ins 里包他人 w:del)自然成立
    for el in reversed(list(root.iter())):
        tag = el.tag
        if el.getparent() is None:
            continue

        if tag in (W + "ins", W + "del") and _is_para_mark(el):
            if not _matches_author(el, author):
                continue
            is_del = tag == W + "del"
            para = el.getparent().getparent().getparent()  # rPr → pPr → p
            _remove(el)
            stats["changes_applied"] += 1
            # 段落标记消失的两种情形:接受删除 / 拒绝插入
            if (accept and is_del) or (not accept and not is_del):
                _merge_with_next_para(para, stats)

        elif tag == W + "ins":
            if el.getparent().tag == W + "trPr":   # 行级标记,由下方表格行循环处理
                continue
            if not _matches_author(el, author):
                continue
            _unwrap(el) if accept else _remove(el)
            stats["changes_applied"] += 1

        elif tag == W + "del":
            if el.getparent().tag == W + "trPr":   # 行级标记,由下方表格行循环处理
                continue
            if not _matches_author(el, author):
                continue
            if accept:
                _remove(el)
            else:
                _rename_deltext(el)
                _unwrap(el)
            stats["changes_applied"] += 1

        elif tag == W + "moveTo" or tag == W + "moveFrom":
            if not _matches_author(el, author):
                continue
            restore = (accept and tag == W + "moveTo") or (not accept and tag == W + "moveFrom")
            if restore:
                _rename_deltext(el)
                _unwrap(el)
            else:
                _remove(el)
            stats["changes_applied"] += 1

        elif tag in _RANGE_MARKERS:
            if _matches_author(el, author):
                _remove(el)

        elif tag in _PROP_CHANGE:
            if not _matches_author(el, author):
                continue
            if accept:
                _remove(el)
            else:
                old = el.find(_PROP_CHANGE[tag])
                holder = el.getparent()  # 当前生效的 rPr/pPr/...
                if old is not None:
                    for child in list(holder):
                        if child is not el:
                            holder.remove(child)
                    for child in list(old):
                        holder.append(child)
                _remove(el)
            stats["changes_applied"] += 1

        elif tag in _CELL_MARKERS:
            if _matches_author(el, author):
                stats["skipped"].append(f"{etree.QName(tag).localname} (cell-level, not handled)")

    # 表格行增删:w:trPr 下的 w:ins / w:del
    for tr_mark in reversed(list(root.iter(W + "trPr"))):
        for tag, drop_row_when in ((W + "del", accept), (W + "ins", not accept)):
            mark = tr_mark.find(tag)
            if mark is None or not _matches_author(mark, author):
                continue
            row = _row_of(tr_mark)
            _remove(mark)
            stats["changes_applied"] += 1
            if drop_row_when and row is not None:
                row.getparent().remove(row)
                stats["rows_removed"] += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("out")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--reject", action="store_true")
    ap.add_argument("--author", default=None, help="只处理该作者的修订(默认全部)")
    args = ap.parse_args()
    mode = "accept" if args.accept else "reject"

    stats = {"mode": mode, "author": args.author, "changes_applied": 0,
             "paragraph_merges": 0, "rows_removed": 0, "skipped": []}

    with zipfile.ZipFile(args.src) as zin:
        parts = {
            n: zin.read(n) for n in zin.namelist()
            if any(fnmatch(n, pat) for pat in _PART_PATTERNS)
        }
        others = [(i, zin.read(i.filename)) for i in zin.infolist()
                  if i.filename not in parts]

    for name, blob in list(parts.items()):
        root = etree.fromstring(blob)
        process_tree(root, mode, args.author, stats)
        parts[name] = etree.tostring(root, xml_declaration=True,
                                     encoding="UTF-8", standalone=True)

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, blob in others:
            zout.writestr(info.filename, blob)
        for name, blob in parts.items():
            zout.writestr(name, blob)

    stats["skipped"] = sorted(set(stats["skipped"]))
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()

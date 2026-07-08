#!/usr/bin/env python3
"""给 .docx 添加批注(comment),锚定到正文中的一段文字 —— 纯 Python。

用法:
    python add_comment.py 输入.docx 输出.docx \\
        --anchor "被批注的原文片段" --text "批注内容" [--author 张三] [--initials ZS]

锚点规则:--anchor 必须能在**单个段落**的可见文本里找到(跨段落请分成两条批注);
命中第一处。批注范围按 run 粒度覆盖(覆盖所有与锚点重叠的 run,可能比锚点略宽,
Word 里高亮区域以 run 为界是正常现象)。

产出的是基础批注(word/comments.xml)。Word 全系可读;不生成 commentsExtended
等扩展部件,因此不支持「回复串/已解决状态」—— 需要回复请在批注文本里引用上下文。
输出末行为 JSON(批注 id + 命中段落序号)。
"""

import argparse
import json
import zipfile
from datetime import datetime, timezone

from lxml import etree

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % NS_W
_COMMENTS_PART = "word/comments.xml"
_RELS_PART = "word/_rels/document.xml.rels"
_CT_PART = "[Content_Types].xml"
_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
_CT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
)


def _run_visible_text(run) -> str:
    # 只数直接子 w:t:run 内嵌 w:pict/文本框时,iter 会把内层段落的文字
    # 混进本 run 的 offset 映射(正常内容里 w:t 都是 w:r 的直接子节点)
    parts = []
    for t in run:
        if t.tag == W + "t":
            parts.append(t.text or "")
    return "".join(parts)


def _para_runs(p):
    """本段落自己的 run —— 不下潜嵌套段落(w:txbxContent 文本框里的 run 属于
    内层 w:p,混进外层 offset 映射会让批注括号锚到错误文字;文本框内的锚点由
    root.iter 迭代到内层 p 时自然命中)。w:hyperlink 等中间层不隔断。"""
    out = []
    for r in p.iter(W + "r"):
        anc = r.getparent()
        while anc is not None and anc.tag != W + "p":
            anc = anc.getparent()
        if anc is p:
            out.append(r)
    return out


def _find_anchor(root, anchor):
    """返回 (段落序号, 首个重叠 run, 末个重叠 run);找不到 → None。"""
    for p_idx, p in enumerate(root.iter(W + "p")):
        runs = _para_runs(p)
        texts = [_run_visible_text(r) for r in runs]
        full = "".join(texts)
        pos = full.find(anchor)
        if pos < 0:
            continue
        end = pos + len(anchor)
        first = last = None
        offset = 0
        for run, text in zip(runs, texts):
            run_start, run_end = offset, offset + len(text)
            offset = run_end
            if run_end <= pos or run_start >= end or not text:
                continue
            if first is None:
                first = run
            last = run
        if first is not None:
            return p_idx, first, last
    return None


def _insert_markers(first_run, last_run, cid):
    start = etree.SubElement(first_run.getparent(), W + "commentRangeStart")
    start.set(W + "id", cid)
    first_run.addprevious(start)

    end = etree.SubElement(last_run.getparent(), W + "commentRangeEnd")
    end.set(W + "id", cid)
    last_run.addnext(end)

    ref_run = etree.SubElement(last_run.getparent(), W + "r")
    ref = etree.SubElement(ref_run, W + "commentReference")
    ref.set(W + "id", cid)
    end.addnext(ref_run)


def _upsert_comment(comments_blob, cid, author, initials, date, text):
    if comments_blob is not None:
        root = etree.fromstring(comments_blob)
    else:
        root = etree.Element(W + "comments", nsmap={"w": NS_W})
    comment = etree.SubElement(root, W + "comment")
    comment.set(W + "id", cid)
    comment.set(W + "author", author)
    comment.set(W + "initials", initials)
    comment.set(W + "date", date)
    p = etree.SubElement(comment, W + "p")
    r = etree.SubElement(p, W + "r")
    t = etree.SubElement(r, W + "t")
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _next_comment_id(comments_blob) -> str:
    if comments_blob is None:
        return "1"
    root = etree.fromstring(comments_blob)
    ids = [int(c.get(W + "id", "0")) for c in root.iter(W + "comment")]
    return str(max(ids, default=0) + 1)


def _ensure_rel(rels_blob):
    root = etree.fromstring(rels_blob)
    R = "{%s}" % _NS_REL
    for rel in root.iter(R + "Relationship"):
        if rel.get("Type") == _REL_TYPE:
            return rels_blob
    nums = [
        int(rel.get("Id")[3:]) for rel in root.iter(R + "Relationship")
        if (rel.get("Id") or "").startswith("rId") and rel.get("Id")[3:].isdigit()
    ]
    rel = etree.SubElement(root, R + "Relationship")
    rel.set("Id", f"rId{max(nums, default=0) + 1}")
    rel.set("Type", _REL_TYPE)
    rel.set("Target", "comments.xml")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _ensure_content_type(ct_blob):
    root = etree.fromstring(ct_blob)
    C = "{%s}" % _NS_CT
    for ov in root.iter(C + "Override"):
        if ov.get("PartName") == "/" + _COMMENTS_PART:
            return ct_blob
    ov = etree.SubElement(root, C + "Override")
    ov.set("PartName", "/" + _COMMENTS_PART)
    ov.set("ContentType", _CT_TYPE)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--anchor", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--author", default="ArtifactFlow")
    ap.add_argument("--initials", default="AF")
    args = ap.parse_args()

    with zipfile.ZipFile(args.src) as zin:
        members = {i.filename: zin.read(i.filename) for i in zin.infolist()}

    doc_root = etree.fromstring(members["word/document.xml"])
    hit = _find_anchor(doc_root, args.anchor)
    if hit is None:
        raise SystemExit(
            f"error: 在任何单个段落的可见文本中都找不到锚点 {args.anchor!r} —— "
            "检查是否跨段落、或与原文用字不一致(全半角/空格)"
        )
    p_idx, first_run, last_run = hit

    cid = _next_comment_id(members.get(_COMMENTS_PART))
    _insert_markers(first_run, last_run, cid)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    members["word/document.xml"] = etree.tostring(
        doc_root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    members[_COMMENTS_PART] = _upsert_comment(
        members.get(_COMMENTS_PART), cid, args.author, args.initials, date, args.text
    )
    members[_RELS_PART] = _ensure_rel(members[_RELS_PART])
    members[_CT_PART] = _ensure_content_type(members[_CT_PART])

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, blob in members.items():
            zout.writestr(name, blob)

    print(json.dumps({"comment_id": int(cid), "paragraph_index": p_idx},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

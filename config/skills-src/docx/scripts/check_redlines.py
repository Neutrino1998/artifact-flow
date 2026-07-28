#!/usr/bin/env python3
"""校验修订写入没有破坏原文 —— 修订编辑的安全网,做完 w:ins/w:del 手术必跑。

用法:
    python check_redlines.py 原始.docx 修改后.docx --author 作者名

原理:把「作者名」的全部修订**回滚**(跳过其 w:ins 内容、还原其 w:del 内容)后
提取的可见文本,必须与原始文档的可见文本逐字相等。相等 → 修订是纯增量标注,
没有静默改写正文;不等 → 打出词级 diff 定位破坏点,退出码 1。

本脚本不验证接受修订后的内容或页面布局。带修订文字不可用 python-docx 的
Paragraph.text/Run.text 验证,因为这些 API 不会可靠包含 w:ins/w:del 内容。

比较仅覆盖 document.xml 正文(不含页眉页脚/脚注);空白折叠比较(段落合并类
修订会扰动换行,不构成破坏)。

已知不覆盖:w:moveFrom/w:moveTo(遇到本作者的 move 会误报 FAIL)——
写侧规范(references/redlines.md)要求移动一律用 w:del + w:ins 对表达,
不产生 move 元素;他人文档里已有的 move 在两份文档中一致,自然抵消。
"""

import argparse
import difflib
import sys
import zipfile

from lxml import etree

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % NS_W


def _visible_text(blob: bytes, revert_author=None):
    """按 Word 显示语义提取段落文本。

    revert_author=None:所见即所得 —— w:ins 内容计入,w:del 内容不计。
    revert_author=X:回滚 X 的修订 —— X 的 w:ins 内容跳过,X 的 w:del 内容计入;
    其他作者的修订仍按所见即所得(它们在两份文档里应当一致)。
    """
    root = etree.fromstring(blob)
    paras = []
    for p in root.iter(W + "p"):
        buf = []
        for node in p.iter():
            if node.tag == W + "t":
                # 所见即所得:ins 内容计入;仅当回滚 X 时跳过 X 自己插入的内容
                if revert_author is not None and _inside(node, p, W + "ins", revert_author):
                    continue
                buf.append(node.text or "")
            elif node.tag == W + "delText":
                # 所见即所得:del 内容不计;仅当回滚 X 时还原 X 自己删掉的内容
                if revert_author is not None and (
                    _enclosing_author(node, p, W + "del") == revert_author
                ):
                    buf.append(node.text or "")
            elif node.tag in (W + "tab",):
                buf.append("\t")
            elif node.tag in (W + "br", W + "cr"):
                buf.append("\n")
        paras.append("".join(buf))
    return paras


def _ancestors(node, stop):
    cur = node.getparent()
    while cur is not None and cur is not stop:
        yield cur
        cur = cur.getparent()


def _inside(node, p, tag, author):
    for anc in _ancestors(node, p):
        if anc.tag == tag:
            return author is None or anc.get(W + "author") == author
    return False


def _enclosing_author(node, p, tag):
    for anc in _ancestors(node, p):
        if anc.tag == tag:
            return anc.get(W + "author")
    return None


def _read_document(path):
    with zipfile.ZipFile(path) as zf:
        return zf.read("word/document.xml")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("original")
    ap.add_argument("edited")
    ap.add_argument("--author", required=True, help="本次修订使用的 w:author 值")
    args = ap.parse_args()

    baseline = _visible_text(_read_document(args.original))
    reverted = _visible_text(_read_document(args.edited), revert_author=args.author)

    norm = lambda paras: " ".join(" ".join(paras).split())  # noqa: E731 — 空白折叠
    if norm(baseline) == norm(reverted):
        print(f"OK: 回滚 '{args.author}' 的修订后与原文一致，未发现静默正文改写。")
        print("范围：本检查仅验证修订完整性，不验证修改内容或页面布局。")
        print(
            "内容核对：使用 Pandoc --track-changes=accept 或 --track-changes=all；"
            "不要使用 python-docx Paragraph.text/Run.text 检查修订文字。"
        )
        print("修改稿未再写入时，无需重复运行本检查，请进入后续步骤。")
        return

    print(f"FAIL: 回滚 '{args.author}' 的修订后与原文不一致 —— 有正文被静默改写!", file=sys.stderr)
    diff = difflib.unified_diff(baseline, reverted, "original", "edited(reverted)", lineterm="")
    for i, line in enumerate(diff):
        if i > 60:
            print("  ... (diff 截断)", file=sys.stderr)
            break
        print(f"  {line}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()

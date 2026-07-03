#!/usr/bin/env python3
"""校验修订写入没有破坏原文 —— 修订编辑的安全网,做完 w:ins/w:del 手术必跑。

用法:
    python check_redlines.py 原始.docx 修改后.docx --author 作者名

原理:把「作者名」的全部修订**回滚**(跳过其 w:ins 内容、还原其 w:del 内容)后
提取的可见文本,必须与原始文档的可见文本逐字相等。相等 → 修订是纯增量标注,
没有静默改写正文;不等 → 打出词级 diff 定位破坏点,退出码 1。

比较仅覆盖 document.xml 正文(不含页眉页脚/脚注);空白折叠比较(段落合并类
修订会扰动换行,不构成破坏)。
"""

import argparse
import difflib
import sys
import xml.etree.ElementTree as ET
import zipfile

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % NS_W


def _visible_text(blob: bytes, revert_author=None):
    """按 Word 显示语义提取段落文本。

    revert_author=None:所见即所得 —— w:ins 内容计入,w:del 内容不计。
    revert_author=X:回滚 X 的修订 —— X 的 w:ins 内容跳过,X 的 w:del 内容计入;
    其他作者的修订仍按所见即所得(它们在两份文档里应当一致)。
    """
    root = ET.fromstring(blob)
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


def _ancestors(node, stop, tree_index):
    cur = tree_index.get(id(node))
    while cur is not None and cur is not stop:
        yield cur
        cur = tree_index.get(id(cur))


# stdlib ET 没有 getparent —— 构建一次 child→parent 索引,挂在函数属性上缓存
def _build_index(p):
    return {id(c): parent for parent in p.iter() for c in parent}


def _inside(node, p, tag, author):
    idx = _INDEX_CACHE.setdefault(id(p), _build_index(p))
    for anc in _ancestors(node, p, idx):
        if anc.tag == tag:
            return author is None or anc.get(W + "author") == author
    return False


def _enclosing_author(node, p, tag):
    idx = _INDEX_CACHE.setdefault(id(p), _build_index(p))
    for anc in _ancestors(node, p, idx):
        if anc.tag == tag:
            return anc.get(W + "author")
    return None


_INDEX_CACHE = {}


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
    _INDEX_CACHE.clear()
    reverted = _visible_text(_read_document(args.edited), revert_author=args.author)

    norm = lambda paras: " ".join(" ".join(paras).split())  # noqa: E731 — 空白折叠
    if norm(baseline) == norm(reverted):
        print(f"OK: 回滚 '{args.author}' 的修订后与原文一致,修订是纯增量标注")
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

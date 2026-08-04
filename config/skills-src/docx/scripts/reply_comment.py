#!/usr/bin/env python3
"""列出 DOCX 正文批注，或给一条指定的主批注追加一级回复。

列出批注：
    python reply_comment.py input.docx --list /workspace/comments.json

回复一条批注：
    python reply_comment.py input.docx output.docx \
        --reply-to 12 --text-file /workspace/reply.txt \
        [--author 审阅] [--initials SY]

每次调用只追加一条回复。父批注必须由 ``--reply-to`` 精确指定；脚本不会按
文字或位置猜测，也不会默认使用第一条批注。仅支持 word/document.xml 中的
主批注及 Office 2013 commentsExtended 一级回复结构；回复已有回复、页眉页脚
批注、已解决线程、不兼容的文档模式、现代批注扩展和损坏的线程关系会受控失败。
"""

from __future__ import annotations

import argparse
import json
import posixpath
import secrets
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import cast

from lxml import etree

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
NS_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
NS_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

W = f"{{{NS_W}}}"
W14 = f"{{{NS_W14}}}"
W15 = f"{{{NS_W15}}}"
MC = f"{{{NS_MC}}}"
R = f"{{{NS_REL}}}"
C = f"{{{NS_CT}}}"

COMMENTS_PART = "word/comments.xml"
DOCUMENT_PART = "word/document.xml"
RELS_PART = "word/_rels/document.xml.rels"
CONTENT_TYPES_PART = "[Content_Types].xml"
DEFAULT_COMMENTS_EXTENDED_PART = "word/commentsExtended.xml"

COMMENTS_EXTENDED_REL_TYPE = (
    "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
)
COMMENTS_EXTENDED_CONTENT_TYPE = "application/vnd.ms-word.commentsExtended+xml"
SETTINGS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
)
WORD_COMPATIBILITY_URI = "http://schemas.microsoft.com/office/word"
DEFAULT_COMPATIBILITY_MODE = 12
MIN_REPLY_COMPATIBILITY_MODE = 15
UNSUPPORTED_MODERN_COMMENT_PARTS = {
    "commentsextensible.xml",
    "commentsid.xml",
    "commentsids.xml",
}


class CommentReplyError(RuntimeError):
    """输入文档无法安全完成指定的单条批注回复。"""


def _parse_xml(blob: bytes, part: str) -> etree._Element:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        remove_blank_text=False,
    )
    try:
        return etree.fromstring(blob, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise CommentReplyError(f"{part} 不是可解析的 XML") from exc


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _read_package(path: Path) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(path) as zin:
            return {info.filename: zin.read(info.filename) for info in zin.infolist()}
    except (FileNotFoundError, zipfile.BadZipFile, KeyError) as exc:
        raise CommentReplyError(f"无法读取 DOCX：{path}") from exc


def _write_package(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, blob in members.items():
            zout.writestr(name, blob)


def _reject_unsupported_comment_parts(members: dict[str, bytes]) -> None:
    unsupported = sorted(
        name
        for name in members
        if posixpath.basename(name).casefold() in UNSUPPORTED_MODERN_COMMENT_PARTS
    )
    if unsupported:
        raise CommentReplyError(
            "文档使用尚未支持的现代批注部件：" + ", ".join(unsupported)
        )


def _comments_by_id(comments_root: etree._Element) -> dict[str, etree._Element]:
    comments: dict[str, etree._Element] = {}
    for comment in comments_root.iter(W + "comment"):
        comment_id = comment.get(W + "id")
        if comment_id is None:
            raise CommentReplyError("comments.xml 中存在没有 w:id 的批注")
        if comment_id in comments:
            raise CommentReplyError(f"comments.xml 中批注 ID {comment_id} 重复")
        comments[comment_id] = comment
    return comments


def _last_paragraph(comment: etree._Element) -> etree._Element | None:
    paragraphs = list(comment.iter(W + "p"))
    return paragraphs[-1] if paragraphs else None


def _paragraph_ids(
    comments: dict[str, etree._Element],
) -> tuple[dict[str, str], set[str]]:
    para_to_comment: dict[str, str] = {}
    used: set[str] = set()
    for comment_id, comment in comments.items():
        paragraph = _last_paragraph(comment)
        if paragraph is None:
            continue
        para_id = paragraph.get(W14 + "paraId")
        if para_id is None:
            continue
        normalized = para_id.upper()
        if len(normalized) != 8 or any(ch not in "0123456789ABCDEF" for ch in normalized):
            raise CommentReplyError(
                f"批注 ID {comment_id} 的 w14:paraId 不是 8 位十六进制值"
            )
        if not 0 < int(normalized, 16) < 0x80000000:
            raise CommentReplyError(
                f"批注 ID {comment_id} 的 w14:paraId 不在合法范围内"
            )
        if normalized in para_to_comment:
            raise CommentReplyError(
                f"批注 ID {comment_id} 与 {para_to_comment[normalized]} 共用 paraId"
            )
        paragraph.set(W14 + "paraId", normalized)
        para_to_comment[normalized] = comment_id
        used.add(normalized)
    return para_to_comment, used


def _extended_by_para_id(
    extended_root: etree._Element | None,
) -> dict[str, etree._Element]:
    if extended_root is None:
        return {}
    if extended_root.tag != W15 + "commentsEx":
        raise CommentReplyError("commentsExtended 部件的根元素不是 w15:commentsEx")
    out: dict[str, etree._Element] = {}
    for item in extended_root.iter(W15 + "commentEx"):
        para_id = (item.get(W15 + "paraId") or "").upper()
        if not para_id:
            raise CommentReplyError("commentsExtended 中存在没有 paraId 的 commentEx")
        if para_id in out:
            raise CommentReplyError(
                f"commentsExtended 中 paraId {para_id} 重复"
            )
        item.set(W15 + "paraId", para_id)
        parent = item.get(W15 + "paraIdParent")
        if parent is not None:
            item.set(W15 + "paraIdParent", parent.upper())
        out[para_id] = item
    return out


def _relationship_target_path(target: str, *, label: str) -> str:
    if target.startswith("/"):
        path = posixpath.normpath(target.lstrip("/"))
    else:
        path = posixpath.normpath(posixpath.join("word", target))
    if not path.startswith("word/") or path.startswith("word/../"):
        raise CommentReplyError(
            f"{label} relationship 指向不支持的位置：{target}"
        )
    return path


def _find_comments_extended_part(
    members: dict[str, bytes], rels_root: etree._Element
) -> str | None:
    matches = [
        rel
        for rel in rels_root.iter(R + "Relationship")
        if rel.get("Type") == COMMENTS_EXTENDED_REL_TYPE
    ]
    if len(matches) > 1:
        raise CommentReplyError("文档包含多个 commentsExtended relationship")
    if not matches:
        if DEFAULT_COMMENTS_EXTENDED_PART in members:
            raise CommentReplyError(
                "文档含有未被 relationship 引用的 commentsExtended.xml"
            )
        return None
    rel = matches[0]
    if rel.get("TargetMode") == "External":
        raise CommentReplyError("commentsExtended relationship 不能是外部关系")
    target = rel.get("Target")
    if not target:
        raise CommentReplyError("commentsExtended relationship 缺少 Target")
    part = _relationship_target_path(target, label="commentsExtended")
    if part not in members:
        raise CommentReplyError(f"commentsExtended relationship 缺少目标部件：{part}")
    return part


def _compatibility_mode(
    members: dict[str, bytes], rels_root: etree._Element
) -> int:
    matches = [
        rel
        for rel in rels_root.iter(R + "Relationship")
        if rel.get("Type") == SETTINGS_REL_TYPE
    ]
    if len(matches) > 1:
        raise CommentReplyError("文档包含多个 settings relationship")
    if not matches:
        return DEFAULT_COMPATIBILITY_MODE
    rel = matches[0]
    if rel.get("TargetMode") == "External":
        raise CommentReplyError("settings relationship 不能是外部关系")
    target = rel.get("Target")
    if not target:
        raise CommentReplyError("settings relationship 缺少 Target")
    part = _relationship_target_path(target, label="settings")
    if part not in members:
        raise CommentReplyError(f"settings relationship 缺少目标部件：{part}")
    settings_root = _parse_xml(members[part], part)
    if settings_root.tag != W + "settings":
        raise CommentReplyError("settings 部件的根元素不是 w:settings")
    compatibility_settings = [
        item
        for item in settings_root.iter(W + "compatSetting")
        if item.get(W + "name") == "compatibilityMode"
        and item.get(W + "uri") == WORD_COMPATIBILITY_URI
    ]
    if not compatibility_settings:
        return DEFAULT_COMPATIBILITY_MODE
    if len(compatibility_settings) > 1:
        raise CommentReplyError("settings 中存在多个 compatibilityMode")
    raw = compatibility_settings[0].get(W + "val")
    try:
        mode = int(raw) if raw is not None else -1
    except ValueError as exc:
        raise CommentReplyError(
            "settings 中的 compatibilityMode 不是非负整数"
        ) from exc
    if mode < 0:
        raise CommentReplyError("settings 中的 compatibilityMode 不是非负整数")
    return mode


def _ensure_comments_extended_relationship(
    rels_root: etree._Element, part: str
) -> None:
    matches = [
        rel
        for rel in rels_root.iter(R + "Relationship")
        if rel.get("Type") == COMMENTS_EXTENDED_REL_TYPE
    ]
    if matches:
        return
    nums = [
        int(rel.get("Id")[3:])
        for rel in rels_root.iter(R + "Relationship")
        if (rel.get("Id") or "").startswith("rId")
        and rel.get("Id")[3:].isdigit()
    ]
    rel = etree.SubElement(rels_root, R + "Relationship")
    rel.set("Id", f"rId{max(nums, default=0) + 1}")
    rel.set("Type", COMMENTS_EXTENDED_REL_TYPE)
    rel.set("Target", posixpath.relpath(part, "word"))


def _ensure_comments_extended_content_type(
    content_types_root: etree._Element, part: str
) -> None:
    part_name = "/" + part
    for override in content_types_root.iter(C + "Override"):
        if override.get("PartName") != part_name:
            continue
        actual = override.get("ContentType")
        if actual != COMMENTS_EXTENDED_CONTENT_TYPE:
            raise CommentReplyError(
                f"{part_name} 的 ContentType 不正确：{actual or '(missing)'}"
            )
        return
    override = etree.SubElement(content_types_root, C + "Override")
    override.set("PartName", part_name)
    override.set("ContentType", COMMENTS_EXTENDED_CONTENT_TYPE)


def _ensure_root_namespaces(root: etree._Element) -> etree._Element:
    required = {"w14": NS_W14, "mc": NS_MC}
    for prefix, namespace in required.items():
        bound = root.nsmap.get(prefix)
        if bound is not None and bound != namespace:
            raise CommentReplyError(
                f"comments.xml 已将前缀 {prefix} 绑定到不兼容的命名空间"
            )
    if all(
        root.nsmap.get(prefix) == namespace
        for prefix, namespace in required.items()
    ):
        upgraded = root
    else:
        nsmap = dict(root.nsmap)
        nsmap.update(required)
        upgraded = etree.Element(root.tag, nsmap=nsmap)
        upgraded.attrib.update(root.attrib)
        upgraded.text = root.text
        upgraded.tail = root.tail
        for child in list(root):
            upgraded.append(child)
    ignorable = (upgraded.get(MC + "Ignorable") or "").split()
    if "w14" not in ignorable:
        ignorable.append("w14")
    upgraded.set(MC + "Ignorable", " ".join(ignorable))
    return upgraded


def _new_para_id(used: set[str]) -> str:
    while True:
        candidate = f"{secrets.randbelow(0x7FFFFFFF) + 1:08X}"
        if candidate not in used:
            used.add(candidate)
            return candidate


def _next_comment_id(comments: dict[str, etree._Element]) -> str:
    try:
        values = [int(comment_id) for comment_id in comments]
    except ValueError as exc:
        raise CommentReplyError("comments.xml 含有非整数批注 ID") from exc
    return str(max(values, default=-1) + 1)


def _comment_text(comment: etree._Element) -> str:
    return "".join(
        node.text or ""
        for node in comment.iter()
        if node.tag in {W + "t", W + "delText"}
    )


def _anchor_text_by_comment(document_root: etree._Element) -> dict[str, str]:
    active: list[str] = []
    chunks: dict[str, list[str]] = defaultdict(list)
    for event, element in etree.iterwalk(document_root, events=("start", "end")):
        if event == "start" and element.tag == W + "commentRangeStart":
            comment_id = element.get(W + "id")
            if comment_id is not None:
                active.append(comment_id)
        elif event == "start" and element.tag in {W + "t", W + "delText"}:
            text = element.text or ""
            for comment_id in active:
                chunks[comment_id].append(text)
        elif event == "start" and element.tag == W + "commentRangeEnd":
            comment_id = element.get(W + "id")
            if comment_id in active:
                active.remove(comment_id)
    return {comment_id: "".join(parts) for comment_id, parts in chunks.items()}


def _supported_body_comment_ids(document_root: etree._Element) -> set[str]:
    """Return comments referenced from the supported main-document body."""
    supported: set[str] = set()
    for reference in document_root.iter(W + "commentReference"):
        comment_id = reference.get(W + "id")
        if comment_id is None:
            continue
        ancestors = {ancestor.tag for ancestor in reference.iterancestors()}
        if W + "body" in ancestors and W + "txbxContent" not in ancestors:
            supported.add(comment_id)
    return supported


def _comment_ex_resolved(item: etree._Element | None) -> bool | None:
    if item is None:
        return None
    done = item.get(W15 + "done")
    return done in {"1", "true", "on"} if done is not None else False


def _reply_block_reason(
    comment_id: str,
    *,
    is_reply: bool,
    has_paragraph: bool,
    in_supported_body: bool,
    compatibility_mode: int,
    resolved: bool | None,
) -> str | None:
    if is_reply:
        return f"仅支持回复主批注，不能回复已有回复（批注 ID {comment_id}）"
    if not has_paragraph:
        return f"父批注 ID {comment_id} 没有可关联的段落"
    if not in_supported_body:
        return f"父批注 ID {comment_id} 不在受支持的正文中"
    if compatibility_mode < MIN_REPLY_COMPATIBILITY_MODE:
        return f"文档兼容模式 {compatibility_mode} 不支持批注回复"
    if resolved is True:
        return f"批注 ID {comment_id} 已解决，请先重新打开该线程"
    return None


def _thread_info(
    comments: dict[str, etree._Element],
    extended_root: etree._Element | None,
) -> tuple[dict[str, str | None], dict[str, bool | None]]:
    para_to_comment, _ = _paragraph_ids(comments)
    extended = _extended_by_para_id(extended_root)
    parents: dict[str, str | None] = {}
    resolved: dict[str, bool | None] = {}
    for comment_id, comment in comments.items():
        paragraph = _last_paragraph(comment)
        para_id = paragraph.get(W14 + "paraId") if paragraph is not None else None
        item = extended.get((para_id or "").upper())
        if item is None:
            parents[comment_id] = None
            resolved[comment_id] = None
            continue
        parent_para = item.get(W15 + "paraIdParent")
        if parent_para is None:
            parents[comment_id] = None
        else:
            parent_id = para_to_comment.get(parent_para.upper())
            if parent_id is None:
                raise CommentReplyError(
                    f"批注 ID {comment_id} 指向不存在的父 paraId {parent_para}"
                )
            parents[comment_id] = parent_id
        resolved[comment_id] = _comment_ex_resolved(item)
    return parents, resolved


def list_comments(path: Path) -> list[dict[str, object]]:
    members = _read_package(path)
    _reject_unsupported_comment_parts(members)
    if COMMENTS_PART not in members:
        return []
    if DOCUMENT_PART not in members or RELS_PART not in members:
        raise CommentReplyError("DOCX 缺少正文或正文 relationship 部件")
    comments_root = _parse_xml(members[COMMENTS_PART], COMMENTS_PART)
    comments = _comments_by_id(comments_root)
    document_root = _parse_xml(members[DOCUMENT_PART], DOCUMENT_PART)
    rels_root = _parse_xml(members[RELS_PART], RELS_PART)
    extended_part = _find_comments_extended_part(members, rels_root)
    extended_root = (
        _parse_xml(members[extended_part], extended_part)
        if extended_part is not None
        else None
    )
    parents, resolved = _thread_info(comments, extended_root)
    anchors = _anchor_text_by_comment(document_root)
    supported_ids = _supported_body_comment_ids(document_root)
    compatibility_mode = _compatibility_mode(members, rels_root)
    result = []
    for comment_id, comment in comments.items():
        paragraph = _last_paragraph(comment)
        parent_id = parents[comment_id]
        block_reason = _reply_block_reason(
            comment_id,
            is_reply=parent_id is not None,
            has_paragraph=paragraph is not None,
            in_supported_body=comment_id in supported_ids,
            compatibility_mode=compatibility_mode,
            resolved=resolved[comment_id],
        )
        result.append(
            {
                "id": comment_id,
                "parent_id": parent_id,
                "author": comment.get(W + "author", ""),
                "initials": comment.get(W + "initials", ""),
                "date": comment.get(W + "date"),
                "text": _comment_text(comment),
                "anchor": anchors.get(comment_id, ""),
                "resolved": resolved[comment_id],
                "replyable": block_reason is None,
            }
        )
    return result


def reply_to_comment(
    source: Path,
    destination: Path,
    *,
    reply_to: str,
    text: str,
    author: str,
    initials: str,
) -> dict[str, object]:
    if source.resolve() == destination.resolve():
        raise CommentReplyError("输出文件必须与输入文件不同")
    if not text.strip():
        raise CommentReplyError("回复内容不能为空")
    members = _read_package(source)
    _reject_unsupported_comment_parts(members)
    required = {COMMENTS_PART, DOCUMENT_PART, RELS_PART, CONTENT_TYPES_PART}
    missing = sorted(required - members.keys())
    if missing:
        raise CommentReplyError(f"DOCX 缺少必要部件：{', '.join(missing)}")

    comments_root = _parse_xml(members[COMMENTS_PART], COMMENTS_PART)
    comments_root = _ensure_root_namespaces(comments_root)
    comments = _comments_by_id(comments_root)
    parent = comments.get(reply_to)
    if parent is None:
        raise CommentReplyError(f"找不到父批注 ID {reply_to}")
    parent_paragraph = _last_paragraph(parent)

    rels_root = _parse_xml(members[RELS_PART], RELS_PART)
    extended_part = _find_comments_extended_part(members, rels_root)
    if extended_part is None:
        extended_part = DEFAULT_COMMENTS_EXTENDED_PART
        extended_root = etree.Element(W15 + "commentsEx", nsmap={"w15": NS_W15})
    else:
        extended_root = _parse_xml(members[extended_part], extended_part)

    _, used_para_ids = _paragraph_ids(comments)
    extended = _extended_by_para_id(extended_root)
    parent_para_id = (
        parent_paragraph.get(W14 + "paraId")
        if parent_paragraph is not None
        else None
    )
    parent_item = extended.get(parent_para_id.upper()) if parent_para_id else None
    is_reply = (
        parent_item is not None
        and parent_item.get(W15 + "paraIdParent") is not None
    )
    if parent_para_id is not None:
        parent_para_id = parent_para_id.upper()
    document_root = _parse_xml(members[DOCUMENT_PART], DOCUMENT_PART)
    block_reason = _reply_block_reason(
        reply_to,
        is_reply=is_reply,
        has_paragraph=parent_paragraph is not None,
        in_supported_body=reply_to in _supported_body_comment_ids(document_root),
        compatibility_mode=_compatibility_mode(members, rels_root),
        resolved=_comment_ex_resolved(parent_item),
    )
    if block_reason is not None:
        raise CommentReplyError(block_reason)
    parent_paragraph = cast(etree._Element, parent_paragraph)
    if parent_para_id is None:
        parent_para_id = _new_para_id(used_para_ids)
        parent_paragraph.set(W14 + "paraId", parent_para_id)
    if parent_item is None:
        parent_item = etree.SubElement(extended_root, W15 + "commentEx")
        parent_item.set(W15 + "paraId", parent_para_id)

    reply_comment_id = _next_comment_id(comments)
    reply_para_id = _new_para_id(used_para_ids)
    reply = etree.SubElement(comments_root, W + "comment")
    reply.set(W + "id", reply_comment_id)
    reply.set(W + "author", author)
    reply.set(W + "initials", initials)
    reply.set(W + "date", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    paragraph = etree.SubElement(reply, W + "p")
    paragraph.set(W14 + "paraId", reply_para_id)
    run = etree.SubElement(paragraph, W + "r")
    text_node = etree.SubElement(run, W + "t")
    text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_node.text = text

    reply_item = etree.SubElement(extended_root, W15 + "commentEx")
    reply_item.set(W15 + "paraId", reply_para_id)
    reply_item.set(W15 + "paraIdParent", parent_para_id)

    _ensure_comments_extended_relationship(rels_root, extended_part)
    content_types_root = _parse_xml(
        members[CONTENT_TYPES_PART], CONTENT_TYPES_PART
    )
    _ensure_comments_extended_content_type(content_types_root, extended_part)

    members[COMMENTS_PART] = _serialize(comments_root)
    members[extended_part] = _serialize(extended_root)
    members[RELS_PART] = _serialize(rels_root)
    members[CONTENT_TYPES_PART] = _serialize(content_types_root)
    _write_package(destination, members)
    return {
        "parent_comment_id": reply_to,
        "reply_comment_id": reply_comment_id,
    }


def _read_text_arg(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    provided = [args.text is not None, args.text_file is not None]
    if sum(provided) != 1:
        parser.error("回复模式必须且只能提供 --text 或 --text-file")
    if args.text_file is None:
        return args.text
    try:
        text = args.text_file.read_text(encoding="utf-8")
    except OSError as exc:
        parser.error(f"无法读取回复文本文件：{exc}")
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    parser.add_argument("out", nargs="?", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", dest="list_out", type=Path, metavar="JSON")
    mode.add_argument("--reply-to", metavar="COMMENT_ID")
    parser.add_argument("--text")
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--author", default="ArtifactFlow")
    parser.add_argument("--initials", default="AF")
    args = parser.parse_args()

    try:
        if args.list_out is not None:
            if args.out is not None or args.text is not None or args.text_file is not None:
                parser.error("列出模式不能提供输出 DOCX 或回复文本")
            if args.src.resolve() == args.list_out.resolve():
                raise CommentReplyError("列表输出文件必须与输入文件不同")
            comments = list_comments(args.src)
            args.list_out.write_text(
                json.dumps(comments, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {"comments": len(comments), "path": str(args.list_out)},
                    ensure_ascii=False,
                )
            )
            return
        if args.out is None:
            parser.error("回复模式必须提供输出 DOCX")
        text = _read_text_arg(args, parser)
        result = reply_to_comment(
            args.src,
            args.out,
            reply_to=args.reply_to,
            text=text,
            author=args.author,
            initials=args.initials,
        )
        print(json.dumps(result, ensure_ascii=False))
    except CommentReplyError as exc:
        parser.exit(2, f"reply_comment: {exc}\n")


if __name__ == "__main__":
    main()

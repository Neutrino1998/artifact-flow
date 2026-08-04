"""DOCX 批注回复必须显式绑定各自父批注。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    from lxml import etree
except ModuleNotFoundError:  # sandbox-only dependency; standard backend CI skips.
    etree = None

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "config" / "skills-src" / "docx" / "scripts" / "reply_comment.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("docx_reply_comment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reply_comment = _load_script() if etree is not None else None

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
NS_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

W = f"{{{NS_W}}}"
W14 = f"{{{NS_W14}}}"
W15 = f"{{{NS_W15}}}"
R = f"{{{NS_REL}}}"
C = f"{{{NS_CT}}}"


def _xml_bytes(root):
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _write_fixture(path: Path) -> None:
    document = etree.Element(W + "document", nsmap={"w": NS_W})
    body = etree.SubElement(document, W + "body")
    for comment_id, text in (("0", "Alpha"), ("1", "Beta")):
        paragraph = etree.SubElement(body, W + "p")
        start = etree.SubElement(paragraph, W + "commentRangeStart")
        start.set(W + "id", comment_id)
        run = etree.SubElement(paragraph, W + "r")
        etree.SubElement(run, W + "t").text = text
        end = etree.SubElement(paragraph, W + "commentRangeEnd")
        end.set(W + "id", comment_id)
        ref_run = etree.SubElement(paragraph, W + "r")
        ref = etree.SubElement(ref_run, W + "commentReference")
        ref.set(W + "id", comment_id)

    comments = etree.Element(W + "comments", nsmap={"w": NS_W})
    for comment_id, text in (("0", "Check alpha"), ("1", "Check beta")):
        comment = etree.SubElement(comments, W + "comment")
        comment.set(W + "id", comment_id)
        comment.set(W + "author", "Reviewer")
        comment.set(W + "initials", "RV")
        paragraph = etree.SubElement(comment, W + "p")
        run = etree.SubElement(paragraph, W + "r")
        etree.SubElement(run, W + "t").text = text

    rels = etree.Element(R + "Relationships", nsmap={None: NS_REL})
    rel = etree.SubElement(rels, R + "Relationship")
    rel.set("Id", "rId1")
    rel.set(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
    )
    rel.set("Target", "comments.xml")

    content_types = etree.Element(C + "Types", nsmap={None: NS_CT})
    for part_name, content_type in (
        (
            "/word/document.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        ),
        (
            "/word/comments.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        ),
    ):
        override = etree.SubElement(content_types, C + "Override")
        override.set("PartName", part_name)
        override.set("ContentType", content_type)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("word/document.xml", _xml_bytes(document))
        package.writestr("word/comments.xml", _xml_bytes(comments))
        package.writestr("word/_rels/document.xml.rels", _xml_bytes(rels))
        package.writestr("[Content_Types].xml", _xml_bytes(content_types))


def _read_part(path: Path, part: str):
    with zipfile.ZipFile(path) as package:
        return etree.fromstring(package.read(part))


def _comment_para_ids(comments_root):
    result = {}
    for comment in comments_root.iter(W + "comment"):
        paragraphs = list(comment.iter(W + "p"))
        result[comment.get(W + "id")] = paragraphs[-1].get(W14 + "paraId")
    return result


@unittest.skipIf(etree is None, "requires the sandbox lxml dependency")
class DocxCommentReplyTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_each_reply_is_bound_to_its_explicit_parent(self):
        source = self.tmp_path / "source.docx"
        first = self.tmp_path / "first.docx"
        second = self.tmp_path / "second.docx"
        _write_fixture(source)

        result_a = reply_comment.reply_to_comment(
            source,
            first,
            reply_to="0",
            text="Reply alpha",
            author="Assistant",
            initials="AI",
        )
        result_b = reply_comment.reply_to_comment(
            first,
            second,
            reply_to="1",
            text="Reply beta",
            author="Assistant",
            initials="AI",
        )

        self.assertEqual(
            result_a, {"parent_comment_id": 0, "reply_comment_id": 2}
        )
        self.assertEqual(
            result_b, {"parent_comment_id": 1, "reply_comment_id": 3}
        )

        comments = _read_part(second, "word/comments.xml")
        para_ids = _comment_para_ids(comments)
        self.assertEqual(set(para_ids), {"0", "1", "2", "3"})
        self.assertEqual(len(set(para_ids.values())), 4)

        extended = _read_part(second, "word/commentsExtended.xml")
        by_para_id = {
            item.get(W15 + "paraId"): item
            for item in extended.iter(W15 + "commentEx")
        }
        self.assertEqual(
            by_para_id[para_ids["2"]].get(W15 + "paraIdParent"), para_ids["0"]
        )
        self.assertEqual(
            by_para_id[para_ids["3"]].get(W15 + "paraIdParent"), para_ids["1"]
        )
        self.assertIsNone(by_para_id[para_ids["0"]].get(W15 + "paraIdParent"))
        self.assertIsNone(by_para_id[para_ids["1"]].get(W15 + "paraIdParent"))

        document = _read_part(second, "word/document.xml")
        anchored_ids = {
            marker.get(W + "id")
            for marker in document.iter(W + "commentRangeStart")
        }
        self.assertEqual(anchored_ids, {"0", "1"})

        rels = _read_part(second, "word/_rels/document.xml.rels")
        extended_rels = [
            rel
            for rel in rels.iter(R + "Relationship")
            if rel.get("Type") == reply_comment.COMMENTS_EXTENDED_REL_TYPE
        ]
        self.assertEqual(len(extended_rels), 1)
        self.assertEqual(extended_rels[0].get("Target"), "commentsExtended.xml")

        content_types = _read_part(second, "[Content_Types].xml")
        overrides = {
            item.get("PartName"): item.get("ContentType")
            for item in content_types.iter(C + "Override")
        }
        self.assertEqual(
            overrides["/word/commentsExtended.xml"],
            reply_comment.COMMENTS_EXTENDED_CONTENT_TYPE,
        )

        listed = {
            str(item["id"]): item for item in reply_comment.list_comments(second)
        }
        self.assertEqual(listed["0"]["anchor"], "Alpha")
        self.assertEqual(listed["1"]["anchor"], "Beta")
        self.assertEqual(listed["2"]["parent_id"], 0)
        self.assertEqual(listed["3"]["parent_id"], 1)
        self.assertIs(listed["0"]["replyable"], True)
        self.assertIs(listed["2"]["replyable"], False)

    def test_replying_to_a_reply_fails_without_output(self):
        source = self.tmp_path / "source.docx"
        first = self.tmp_path / "first.docx"
        invalid = self.tmp_path / "invalid.docx"
        _write_fixture(source)
        reply_comment.reply_to_comment(
            source,
            first,
            reply_to="0",
            text="First reply",
            author="Assistant",
            initials="AI",
        )

        with self.assertRaisesRegex(
            reply_comment.CommentReplyError, "不能回复已有回复"
        ):
            reply_comment.reply_to_comment(
                first,
                invalid,
                reply_to="2",
                text="Nested reply",
                author="Assistant",
                initials="AI",
            )

        self.assertFalse(invalid.exists())

    def test_list_mode_writes_comment_ids_and_anchors(self):
        source = self.tmp_path / "source.docx"
        output = self.tmp_path / "comments.json"
        _write_fixture(source)

        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(source), "--list", str(output)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout), {"comments": 2, "path": str(output)}
        )
        listed = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            [(item["id"], item["anchor"]) for item in listed],
            [(0, "Alpha"), (1, "Beta")],
        )

    def test_empty_reply_fails_without_output(self):
        source = self.tmp_path / "source.docx"
        invalid = self.tmp_path / "invalid.docx"
        _write_fixture(source)

        with self.assertRaisesRegex(
            reply_comment.CommentReplyError, "回复内容不能为空"
        ):
            reply_comment.reply_to_comment(
                source,
                invalid,
                reply_to="0",
                text="  \n",
                author="Assistant",
                initials="AI",
            )

        self.assertFalse(invalid.exists())

    def test_modern_comment_parts_fail_loud_without_output(self):
        source = self.tmp_path / "source.docx"
        invalid = self.tmp_path / "invalid.docx"
        _write_fixture(source)
        with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as package:
            package.writestr("word/commentsIds.xml", b"<unsupported/>")

        with self.assertRaisesRegex(
            reply_comment.CommentReplyError, "尚未支持的现代批注部件"
        ):
            reply_comment.reply_to_comment(
                source,
                invalid,
                reply_to="0",
                text="Reply",
                author="Assistant",
                initials="AI",
            )

        self.assertFalse(invalid.exists())


if __name__ == "__main__":
    unittest.main()

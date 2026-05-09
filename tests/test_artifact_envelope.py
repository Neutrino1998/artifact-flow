"""
Tests for src/tools/artifact_envelope.py

公共 envelope renderer 的纯单元测试。覆盖四种 truncated_by 状态、可空 attribute
省略、body / title 不转义（确保 update_artifact 匹配能成功）、空 body。
"""

import pytest

from tools.artifact_envelope import (
    ArtifactSlice,
    render_artifact_slice,
    make_preview_slice,
)


# ============================================================
# render_artifact_slice
# ============================================================

class TestRenderArtifactSlice:
    def test_minimal_slice_not_truncated(self):
        """没有可空字段 + truncated_by=none 的最简形态。"""
        slice = ArtifactSlice(
            id="abc",
            version=1,
            content_type="text/plain",
            source="agent",
            title="Hello",
            body="world",
            total_chars=5,
            shown_chars=5,
        )
        out = render_artifact_slice(slice)
        # 必填 attribute 都在
        assert 'id="abc"' in out
        assert 'version="1"' in out
        assert 'type="text/plain"' in out
        assert 'source="agent"' in out
        assert 'total_chars="5"' in out
        assert 'shown_chars="5"' in out
        assert 'truncated_by="none"' in out
        assert 'has_more="false"' in out
        # 可空字段省略
        assert 'total_lines' not in out
        assert 'shown_lines' not in out
        assert 'hint' not in out
        assert 'updated_at' not in out
        # body 和 title 原文出现
        assert '<title>Hello</title>' in out
        assert '\nworld\n' in out

    def test_full_slice_with_all_fields(self):
        slice = ArtifactSlice(
            id="abc",
            version=2,
            content_type="text/markdown",
            source="tool",
            title="Output of web_fetch",
            body="lorem ipsum",
            total_chars=48230,
            shown_chars=1000,
            total_lines=847,
            shown_lines=(1, 187),
            truncated_by="char_limit",
            has_more=True,
            hint="Use read_artifact(id='abc', offset=188) to continue",
            updated_at="2026-05-09T12:00:00",
        )
        out = render_artifact_slice(slice)
        assert 'total_lines="847"' in out
        assert 'shown_lines="1-187"' in out
        assert 'truncated_by="char_limit"' in out
        assert 'has_more="true"' in out
        assert 'hint="Use read_artifact(id=\'abc\', offset=188) to continue"' in out
        assert 'updated_at="2026-05-09T12:00:00"' in out

    def test_truncated_by_preview(self):
        slice = ArtifactSlice(
            id="x", version=1, content_type="text/plain", source="agent",
            title="t", body="b",
            total_chars=10000, shown_chars=200,
            truncated_by="preview", has_more=True,
        )
        out = render_artifact_slice(slice)
        assert 'truncated_by="preview"' in out
        assert 'has_more="true"' in out

    def test_truncated_by_line_limit(self):
        slice = ArtifactSlice(
            id="x", version=1, content_type="text/plain", source="agent",
            title="t", body="b",
            total_chars=100, shown_chars=50,
            shown_lines=(1, 5),
            truncated_by="line_limit", has_more=True,
        )
        out = render_artifact_slice(slice)
        assert 'truncated_by="line_limit"' in out

    def test_invalid_truncated_by_rejected(self):
        with pytest.raises(ValueError, match="Invalid truncated_by"):
            ArtifactSlice(
                id="x", version=1, content_type="text/plain", source="agent",
                title="t", body="b",
                total_chars=1, shown_chars=1,
                truncated_by="invalid_value",
            )

    def test_body_not_escaped(self):
        """关键测试：body 含 <, &, " 等字符必须原文输出。

        update_artifact(old_string=...) 用 read 出的内容作匹配源，
        若 body 被 escape，模型回填的 old_string 永远匹配不上原始 content。
        """
        body = "<script>alert(\"x\")</script> & special"
        slice = ArtifactSlice(
            id="x", version=1, content_type="text/html", source="agent",
            title="t", body=body,
            total_chars=len(body), shown_chars=len(body),
        )
        out = render_artifact_slice(slice)
        assert body in out
        assert "&lt;" not in out
        assert "&amp;" not in out
        assert "&quot;" not in out

    def test_title_not_escaped(self):
        """title 同样不转义（user_upload 文件名虽可能含 &，转义会与 body 不一致）。"""
        title = "Q&A draft <v2>"
        slice = ArtifactSlice(
            id="x", version=1, content_type="text/plain", source="user_upload",
            title=title, body="b",
            total_chars=1, shown_chars=1,
        )
        out = render_artifact_slice(slice)
        assert f"<title>{title}</title>" in out

    def test_empty_body(self):
        slice = ArtifactSlice(
            id="x", version=1, content_type="text/plain", source="agent",
            title="t", body="",
            total_chars=0, shown_chars=0,
        )
        out = render_artifact_slice(slice)
        # body 是空，渲染后中间应该是 \n\n（title 行后接换行接空 body 接换行）
        assert "<title>t</title>\n\n</artifact_slice>" in out

    def test_shown_lines_format(self):
        slice = ArtifactSlice(
            id="x", version=1, content_type="text/plain", source="agent",
            title="t", body="b",
            total_chars=1, shown_chars=1,
            shown_lines=(10, 14),
        )
        out = render_artifact_slice(slice)
        assert 'shown_lines="10-14"' in out

    def test_attribute_order_stable(self):
        """attribute 顺序固定（便于 prompt cache 稳定）。"""
        slice = ArtifactSlice(
            id="x", version=1, content_type="text/plain", source="agent",
            title="t", body="b",
            total_chars=1, shown_chars=1,
        )
        out = render_artifact_slice(slice)
        # 验证 id 在 version 前、source 在 total_chars 前
        assert out.index('id=') < out.index('version=')
        assert out.index('source=') < out.index('total_chars=')
        assert out.index('truncated_by=') < out.index('has_more=')


# ============================================================
# make_preview_slice
# ============================================================

class TestMakePreviewSlice:
    def test_content_under_preview_len(self):
        """全文长度 ≤ preview_len → 不截断，has_more=false。"""
        full = "short content"
        slice = make_preview_slice(
            artifact_id="x", version=1, content_type="text/plain",
            source="agent", title="t",
            full_content=full, preview_len=200,
        )
        assert slice.body == full
        assert slice.shown_chars == len(full)
        assert slice.total_chars == len(full)
        assert slice.truncated_by == "none"
        assert slice.has_more is False

    def test_content_over_preview_len(self):
        """全文长度 > preview_len → 截断，truncated_by=preview。"""
        full = "x" * 1000
        slice = make_preview_slice(
            artifact_id="x", version=1, content_type="text/plain",
            source="tool", title="Output of web_fetch",
            full_content=full, preview_len=200,
        )
        assert slice.body == "x" * 200
        assert slice.shown_chars == 200
        assert slice.total_chars == 1000
        assert slice.truncated_by == "preview"
        assert slice.has_more is True

    def test_content_exactly_preview_len(self):
        """边界：全文长度 == preview_len → 不截断。"""
        full = "y" * 200
        slice = make_preview_slice(
            artifact_id="x", version=1, content_type="text/plain",
            source="agent", title="t",
            full_content=full, preview_len=200,
        )
        assert slice.truncated_by == "none"
        assert slice.has_more is False

    def test_hint_passed_through(self):
        slice = make_preview_slice(
            artifact_id="abc", version=1, content_type="text/plain",
            source="tool", title="t",
            full_content="x" * 500, preview_len=100,
            hint="Use read_artifact(id='abc') for full content",
        )
        assert slice.hint == "Use read_artifact(id='abc') for full content"

    def test_renders_correctly(self):
        """make_preview_slice 输出经 render_artifact_slice 后的 XML 格式正确。"""
        full = "long" * 100  # 400 chars
        slice = make_preview_slice(
            artifact_id="abc", version=2, content_type="text/markdown",
            source="tool", title="Output of web_fetch",
            full_content=full, preview_len=100,
            hint="hint text",
        )
        xml = render_artifact_slice(slice)
        assert 'truncated_by="preview"' in xml
        assert 'has_more="true"' in xml
        assert 'total_chars="400"' in xml
        assert 'shown_chars="100"' in xml
        assert 'hint="hint text"' in xml

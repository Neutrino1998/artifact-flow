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

    def test_title_escaped(self):
        """title 转义 &/<(不参与匹配,可安全 escape):不可信文件名(web_fetch 解码
        URL path / 上传用户文件名)含 `</title>` 不能错位 slice 结构。"""
        # 恶意文件名:解码后含闭合标签 + 注入
        title = "x</title><injected> Q&A.pdf"
        slice = ArtifactSlice(
            id="x", version=1, content_type="application/pdf", source="tool",
            title=title, body="b",
            total_chars=1, shown_chars=1,
        )
        out = render_artifact_slice(slice)
        # 元素内容只需转义 < 与 &(> 在内容里无结构意义,原样保留)
        assert "<title>x&lt;/title>&lt;injected> Q&amp;A.pdf</title>" in out
        # 注入的裸闭合/开标签不得出现(< 已被转义,无法错位 slice 结构)
        assert "x</title>" not in out
        assert "<injected>" not in out

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

    def test_attribute_value_escapes_quote(self):
        """Layer B 防御：attribute 值中的 `"` 必须转义，避免破 envelope 边界。

        ID 校验是上游屏障（Layer A），envelope 这层是 defense in depth——
        即使有 bug 让脏 id 漏到这里，也不能让结构错位。
        """
        slice = ArtifactSlice(
            id='evil"id',
            version=1,
            content_type='text/plain',
            source='agent',
            title="t", body="b",
            total_chars=1, shown_chars=1,
            hint='do read_artifact(id="weird") then continue',
            updated_at='2024-"01-01',
        )
        out = render_artifact_slice(slice)
        # 原始引号不应出现在 attribute value 内（会破边界）
        assert 'id="evil"id"' not in out
        assert 'id="evil&quot;id"' in out
        # hint 同理
        assert 'hint="do read_artifact(id=&quot;weird&quot;) then continue"' in out
        # updated_at 同理
        assert 'updated_at="2024-&quot;01-01"' in out

    def test_attribute_value_escapes_amp_and_lt(self):
        """Layer B 防御:attribute 值中的 `&` / `<` 也必须转义,产出始终良构 XML。

        content_type 等字段可能携带不可信来源(如远端 Content-Type 头),含 `&`/`<`
        会让 envelope 非良构。`&` 必须先转义,避免把 `&quot;`/`&lt;` 自己再编码一次。
        """
        slice = ArtifactSlice(
            id="x", version=1,
            content_type="application/pdf&evil<x>",
            source="tool",
            title="t", body="b",
            total_chars=1, shown_chars=1,
        )
        out = render_artifact_slice(slice)
        assert 'type="application/pdf&amp;evil&lt;x>"' in out
        # 裸 & / < 不应残留在 attribute 区(title/body 在元素体、按设计原文,不在此断言)
        assert "pdf&evil" not in out
        assert "&lt;x" in out

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

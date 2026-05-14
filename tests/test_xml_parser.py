"""
Tests for src/tools/xml_parser.py

覆盖：
- 5 个现有 repair 触发时登记 warning（祈使句、指明正确写法）
- 新增 _repair_truncated_cdata 三类场景（CDATA 中间切 / ]]> 后切 / 直接文本切）
- 截断时 partial content 不被静默丢弃
- 正常解析路径不带 warning
"""

import pytest

from tools.xml_parser import parse_tool_calls


# ============================================================
# Normal path — no warnings
# ============================================================

class TestNormalParse:
    def test_complete_tool_call_no_warnings(self):
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[Body.]]></content>
</params>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "create_artifact"
        assert tc.params == {"id": "doc_xxx", "content": "Body."}
        assert tc.error is None
        assert tc.warnings == []


# ============================================================
# Truncation repairs (the original bug)
# ============================================================

class TestTruncationRepair:
    def test_cdata_mid_content_truncation_preserves_partial_content(self):
        """CDATA 中间被切：partial content 必须被保留，不能静默丢失。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[# Doc Title

This long content was cut here because max_tokens hit and"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "create_artifact"
        assert tc.error is None
        assert tc.params["id"] == "doc_xxx"
        # partial content 必须保留
        assert "This long content was cut here" in tc.params["content"]
        # 截断 warning 必须存在并提到 update_artifact
        assert any("truncated" in w.lower() and "update_artifact" in w for w in tc.warnings), (
            f"missing actionable truncation warning, got: {tc.warnings}"
        )

    def test_cdata_closed_but_tag_unclosed_at_tail(self):
        """]]> 写了但 </content> 缺失且尾部无后续标签 —— 现有 _repair_unclosed_cdata_tags
        不触发（要求尾随标签），新 repair 必须接住。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[Body text here.]]>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.params.get("content") == "Body text here."
        assert any("truncated" in w.lower() for w in tc.warnings)

    def test_plain_text_field_truncated_no_cdata(self):
        """字段不用 CDATA、纯文本写到一半被切。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<title>some partial title"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        # title 应该被补全并保留 partial 值
        assert tc.params.get("title") == "some partial title"
        assert any("truncated" in w.lower() for w in tc.warnings)

    def test_complete_call_with_missing_tool_call_close_no_truncation_warning(self):
        """只是漏了 </tool_call> 但所有字段闭合 —— 不应触发截断 warning。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[Body.]]></content>
</params>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.params == {"id": "doc_xxx", "content": "Body."}
        # 不应该有 truncation warning（CDATA 和字段都正常闭合了）
        assert not any("truncated" in w.lower() for w in tc.warnings), (
            f"spurious truncation warning, got: {tc.warnings}"
        )


# ============================================================
# Existing repairs now register warnings
# ============================================================

class TestRepairWarnings:
    def test_tag_equals_syntax_registers_warning(self):
        text = """<tool_call>
<name=call_subagent</name>
<params>
<agent_name><![CDATA[research_agent]]></agent_name>
<instruction><![CDATA[do something]]></instruction>
</params>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "call_subagent"
        # warning 必须明确指出"不要用 = "
        assert any("=" in w and "name" in w.lower() for w in tc.warnings), (
            f"warning missing concrete bad/good example, got: {tc.warnings}"
        )

    def test_tool_name_as_outer_tag_registers_warning(self):
        text = """<tool_call>
<web_fetch>
<params>
<url><![CDATA[https://example.com]]></url>
</params>
</web_fetch>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "web_fetch"
        assert any("web_fetch" in w and "<name>" in w for w in tc.warnings)

    def test_missing_params_close_registers_warning(self):
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[Body.]]></content>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.params == {"id": "doc_xxx", "content": "Body."}
        assert any("</params>" in w for w in tc.warnings)

    def test_scattered_params_registers_warning(self):
        text = """<tool_call>
<name>create_artifact</name>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[Body.]]></content>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.params == {"id": "doc_xxx", "content": "Body."}
        assert any("params" in w.lower() and "single" in w.lower() for w in tc.warnings)

    def test_unclosed_cdata_with_following_tag_registers_warning(self):
        """<content><![CDATA[X]]> 后没 </content> 但后面紧跟其他标签。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<content><![CDATA[Body.]]>
<id><![CDATA[doc_xxx]]></id>
</params>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.params.get("content") == "Body."
        assert tc.params.get("id") == "doc_xxx"
        # _repair_unclosed_cdata_tags warning
        assert any("]]>" in w and "</content>" in w.lower() or "close the field tag" in w.lower() for w in tc.warnings)


# ============================================================
# Malformed — still produces error tool_call
# ============================================================

class TestMalformed:
    def test_unparseable_block_returns_malformed(self):
        text = """<tool_call>
just random text not even XML at all
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "__malformed__"
        assert tc.error is not None


# ============================================================
# format_result renders parser_warnings before <data>
# ============================================================

class TestFormatResultWarnings:
    def test_warnings_render_before_data_block(self):
        from tools.xml_formatter import format_result

        out = format_result("create_artifact", {
            "success": True,
            "data": "Artifact 'doc_xxx' v1 created.",
            "parser_warnings": [
                "Output truncated mid-content (likely max_tokens hit). Use update_artifact to continue.",
                "Missing </params> closing tag. Always close <params> explicitly.",
            ],
        })
        # parser_warnings 块在 data 之前
        assert "<parser_warnings>" in out
        assert "</parser_warnings>" in out
        wi = out.index("<parser_warnings>")
        di = out.index("<data>")
        assert wi < di, f"parser_warnings should precede <data>; got:\n{out}"
        # 警告以 bullet 形式列出
        assert "- Output truncated" in out
        assert "- Missing </params>" in out

    def test_no_warnings_omits_parser_warnings_block(self):
        from tools.xml_formatter import format_result

        out = format_result("create_artifact", {
            "success": True,
            "data": "ok",
            "parser_warnings": [],
        })
        assert "<parser_warnings>" not in out

    def test_missing_parser_warnings_key_safe(self):
        from tools.xml_formatter import format_result

        out = format_result("foo", {"success": True, "data": "ok"})
        assert "<parser_warnings>" not in out

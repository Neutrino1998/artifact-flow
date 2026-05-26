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
        """CDATA 中间被切：partial content 必须被保留，不能静默丢失。
        warning 必须给出可执行的 update_artifact(id, old_str, new_str) anchor 续写指引
        —— 不能用不存在的参数（如 new_content），否则模型按提示调用直接失败。"""
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
        # 截断 warning 必须用实际可执行的参数名（old_str/new_str）
        warning = next((w for w in tc.warnings if "truncated" in w.lower()), None)
        assert warning is not None, f"missing truncation warning, got: {tc.warnings}"
        assert "update_artifact" in warning
        assert "old_str" in warning and "new_str" in warning, (
            f"warning must reference actual update_artifact params (old_str/new_str), got: {warning}"
        )
        # 显式禁止 retry create_artifact / 使用 rewrite_artifact（两者都会再次截断）
        assert "rewrite_artifact" in warning.lower(), (
            f"warning should explicitly warn against rewrite_artifact, got: {warning}"
        )
        # 不应该出现 update_artifact 不存在的参数
        assert "new_content" not in warning, (
            f"warning must not reference a non-existent param (new_content), got: {warning}"
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

    def test_unclosed_field_tag_with_sibling_after_is_not_truncation(self):
        """漏写 </content> 但后面还有 sibling 标签（_repair_unclosed_cdata_tags 的场景）
        —— 不应被误判为截断。会诱导模型对实际完整的内容做不必要的 update_artifact。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<content><![CDATA[Body content here.]]>
<id><![CDATA[doc_xxx]]></id>
</params>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        # content/id 都该被正确解析（_repair_unclosed_cdata_tags 把 </content> 插在正确位置）
        assert tc.params.get("content") == "Body content here."
        assert tc.params.get("id") == "doc_xxx"
        # 不应该有截断 warning（这不是截断，是漏写闭合标签 —— 既有 repair 处理）
        assert not any("truncated" in w.lower() for w in tc.warnings), (
            f"spurious truncation warning on abandoned-mid-content case, got: {tc.warnings}"
        )

    def test_truncated_field_with_embedded_literal_tags_fails_loudly(self):
        """截断发生在 new_str 内，且 artifact 内容本身含字面 </content>/</params>/</tool_call>
        （task_plan 这类 XML 式正文常见）。两道非贪婪正则（<tool_call>(.*?)</tool_call> /
        <params>(.*?)</params>）对 CDATA 盲，会在字面标签处提前收口、丢掉被截断的 new_str。

        正确行为：不再 lossy 地抠出半套参数报误导性的 'Missing new_str'，而是老实报截断，
        让模型缩小输出。回归自内网 2026-05 观测（update_artifact 8 条 'Missing new_str' 里 7
        条实为截断）。"""
        text = """<tool_call>
<name>update_artifact</name>
<params>
<id><![CDATA[task_plan]]></id>
<old_str><![CDATA[10. done

</content>
</team_task_plan>]]></old_str>
<new_str><![CDATA[10. done
11. wip

</content>
</team_task_plan>
</params>
</tool_call>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        # 报错而非静默丢字段：不能再出现"抠到 id/old_str、独独缺 new_str"的误导态
        assert tc.error is not None
        assert "truncat" in tc.error.lower() or "incomplete" in tc.error.lower()
        assert tc.params == {}, f"must not hand back a half-extracted param set, got: {tc.params}"
        # 工具名保留，便于 observability 仍归到 update_artifact 名下
        assert tc.name == "update_artifact"

    def test_partial_content_still_preserved_when_repair_yields_valid_xml(self):
        """对照组：截断但内容不含字面结构标签 → repair 后 etree 成功 → partial content
        必须照旧保留（B 不能误伤 create/rewrite 的有用兜底）。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[# Title

partial body cut mid-sentence and"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.error is None
        assert tc.params["id"] == "doc_xxx"
        assert "partial body cut mid-sentence" in tc.params["content"]
        assert any("truncated" in w.lower() for w in tc.warnings)


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

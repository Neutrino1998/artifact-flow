"""
Tests for src/tools/xml_parser.py

覆盖：
- CDATA-aware 拆分：内容含字面 </tool_call>/</params> 不再误拆/误修（Issue 1）
- 方案1 截断处理：尾部截断块一律报清晰截断错（不 salvage、不存 partial），且只影响最后一个块
- 现有 repair 触发时登记 warning（祈使句、指明正确写法）
- 废弃 lossy _fallback_parse 后：救不活即诚实 __malformed__，不捏造残缺参数
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
    """方案1：尾部截断块一律报清晰截断错（不再 salvage / 不存 partial）。截断只可能命中
    拆分层判定的 trailing 块；complete 块（有 CDATA 外的 </tool_call>）永不按截断处理。"""

    def _assert_truncation_error(self, tc, name):
        assert tc.error is not None
        assert "truncat" in tc.error.lower() or "incomplete" in tc.error.lower()
        assert tc.params == {}, f"截断块不得抠出半套参数, got: {tc.params}"
        assert tc.name == name, f"工具名应保留以便 observability 归类, got: {tc.name}"

    def test_cdata_mid_content_truncation_errors(self):
        """trailing 块、CDATA 中途被切（无 ]]>）→ 截断错（不再保留 partial content）。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[# Doc Title

This long content was cut here because max_tokens hit and"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        self._assert_truncation_error(results[0], "create_artifact")

    def test_cdata_closed_but_field_unclosed_at_tail_errors(self):
        """]]> 写了但 </content> 缺失、尾部无后续标签（case B）→ 截断错。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[Body text here.]]>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        self._assert_truncation_error(results[0], "create_artifact")

    def test_plain_text_field_truncated_errors(self):
        """字段不用 CDATA、纯文本写到一半被切（case C）→ 截断错。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<title>some partial title"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        self._assert_truncation_error(results[0], "create_artifact")

    def test_truncated_field_with_embedded_literal_tags_fails_loudly(self):
        """截断发生在 new_str 内，且 artifact 正文本身含字面 </content>/</params>/</tool_call>
        （task_plan 这类 XML 式正文常见）。旧版两道非贪婪正则对 CDATA 盲，会在字面标签处提前
        收口、丢掉被截断的 new_str → 误报 'Missing new_str'。新行为：老实报截断。
        回归自内网 2026-05 观测（update_artifact 8 条 'Missing new_str' 里 7 条实为截断）。"""
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
        self._assert_truncation_error(results[0], "update_artifact")

    def test_complete_call_with_missing_tool_call_close_succeeds(self):
        """只漏了 </tool_call> 但所有字段都闭合 —— trailing 块但非截断，应正常解析成功。"""
        text = """<tool_call>
<name>create_artifact</name>
<params>
<id><![CDATA[doc_xxx]]></id>
<content><![CDATA[Body.]]></content>
</params>"""
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.error is None
        assert tc.params == {"id": "doc_xxx", "content": "Body."}

    def test_unclosed_field_tag_with_sibling_after_is_not_truncation(self):
        """complete 块，漏写 </content> 但后面还有 sibling 标签（_repair_unclosed_cdata_tags
        的场景）—— 不是截断，应被 repair 正确补全并解析。"""
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
        assert tc.error is None
        assert tc.params.get("content") == "Body content here."
        assert tc.params.get("id") == "doc_xxx"
        assert not any("truncated" in w.lower() for w in tc.warnings), (
            f"spurious truncation warning on non-truncation case, got: {tc.warnings}"
        )


# ============================================================
# CDATA-aware split (Issue 1) + 废弃 fallback 后的诚实失败
# ============================================================

class TestCdataAwareSplit:
    def test_literal_tool_call_close_in_content_not_misplit(self):
        """完整调用，content 的 CDATA 里含字面 </tool_call>（如讲解工具格式的文档）。
        旧版 <tool_call>(.*?)</tool_call> 非贪婪会在字面处腰斩；新拆分跳过 CDATA → 一个完整块。"""
        text = ("<tool_call><name>create_artifact</name><params>"
                "<id><![CDATA[doc]]></id>"
                "<content><![CDATA[wrap calls in </tool_call> tags]]></content>"
                "</params></tool_call>")
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.error is None
        assert tc.name == "create_artifact"
        assert tc.params["content"] == "wrap calls in </tool_call> tags"

    def test_literal_params_close_in_content_with_missing_real_close(self):
        """完整调用、真 </params> 漏写，但 content 里有字面 </params>。旧
        _repair_missing_closing_tags 被字面量骗（以为已闭合）→ 不补 → etree 失败。
        新版在 masked 串上判定 → 补真 </params> → 解析成功。"""
        text = ("<tool_call><name>create_artifact</name><params>"
                "<id><![CDATA[doc]]></id>"
                "<content><![CDATA[mentions </params> literally]]></content>"
                "</tool_call>")
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.error is None
        assert tc.params["id"] == "doc"
        assert tc.params["content"] == "mentions </params> literally"

    def test_multiple_calls_only_last_can_truncate(self):
        """[完整 A][尾部截断 B]：A 正常解析，B 报截断错（截断只影响最后一个块）。"""
        text = ("<tool_call><name>read_artifact</name><params>"
                "<id><![CDATA[a]]></id></params></tool_call>\n"
                "<tool_call><name>create_artifact</name><params>"
                "<id><![CDATA[b]]></id><content><![CDATA[half cut")
        results = parse_tool_calls(text)
        assert len(results) == 2
        assert results[0].name == "read_artifact" and results[0].error is None
        assert results[0].params == {"id": "a"}
        assert results[1].name == "create_artifact" and results[1].error is not None
        assert ("truncat" in results[1].error.lower()
                or "incomplete" in results[1].error.lower())

    def test_non_cdata_special_chars_fail_malformed_not_fabricated(self):
        """废弃 _fallback_parse 后：不用 CDATA 且含 & < 的内容，etree 解析失败、repair 也救不了
        → 诚实返回 __malformed__（而非旧 fallback 正则捏造参数）。"""
        text = ("<tool_call><name>create_artifact</name><params>"
                "<content>a < b & c</content></params></tool_call>")
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "__malformed__"
        assert tc.error is not None


# ============================================================
# 需要 repair 且结果无参的合法调用不应误判 __malformed__（review P2 回归）
# ============================================================

class TestParamlessRepairableCalls:
    def test_tag_equals_syntax_with_empty_params(self):
        """<name=ping</name> 需要 = 修复，params 为空 —— 修复后应返回 ping/{}，而非 __malformed__。"""
        text = "<tool_call><name=ping</name><params></params></tool_call>"
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "ping"
        assert tc.params == {}
        assert tc.error is None

    def test_tool_name_as_tag_with_empty_params(self):
        """<ping><params></params></ping> 需要 tool-name-as-tag 修复，params 为空 —— 同上。"""
        text = "<tool_call><ping><params></params></ping></tool_call>"
        results = parse_tool_calls(text)
        assert len(results) == 1
        tc = results[0]
        assert tc.name == "ping"
        assert tc.params == {}
        assert tc.error is None


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

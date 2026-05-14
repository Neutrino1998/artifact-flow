"""
XML工具调用解析器
使用标准 xml.etree.ElementTree，支持 CDATA
"""

import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """工具调用数据结构"""
    name: str
    params: Dict[str, Any]
    raw_text: str = ""
    error: Optional[str] = None  # 解析失败时的错误信息
    # 解析时触发的兜底修复提示（祈使句，回传给模型在下一轮看到）。
    # 仅在 repair 实际改写了输入时登记；正常解析路径为空列表。
    warnings: List[str] = field(default_factory=list)


class XMLToolCallParser:
    """XML工具调用解析器"""

    @staticmethod
    def parse_tool_calls(text: str) -> List[ToolCall]:
        """
        解析所有 tool_call 块

        Args:
            text: 包含工具调用的文本

        Returns:
            解析出的工具调用列表
        """
        results = []

        # 提取所有 <tool_call>...</tool_call> 块
        pattern = r'<tool_call>(.*?)</tool_call>'
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)

        for match in matches:
            tool_call = XMLToolCallParser._parse_single_block(match)
            if tool_call:
                tool_call.raw_text = f"<tool_call>{match}</tool_call>"
                results.append(tool_call)

        # 检查末尾是否有未闭合的 <tool_call>（小模型容易漏掉 </tool_call>）
        all_opens = [m.start() for m in re.finditer(r'<tool_call>', text, re.IGNORECASE)]
        all_closes = [m.start() for m in re.finditer(r'</tool_call>', text, re.IGNORECASE)]
        if all_opens and (not all_closes or all_opens[-1] > all_closes[-1]):
            start = all_opens[-1] + len('<tool_call>')
            unclosed_content = text[start:]
            tool_call = XMLToolCallParser._parse_single_block(unclosed_content)
            if tool_call:
                tool_call.raw_text = text[all_opens[-1]:]
                results.append(tool_call)

        return results

    @staticmethod
    def _parse_single_block(content: str) -> Optional[ToolCall]:
        """
        解析单个 tool_call 块

        返回 None 仅当 content 为空白（无实际内容）。
        若有内容但无法解析，返回带 error 的 ToolCall（让 engine 反馈给 agent）。
        若内容能解析但触发了 repair 兜底，warnings 会带上祈使句提示给模型。
        """
        # 空白内容 → 跳过
        if not content.strip():
            return None

        # 先尝试标准 XML 解析
        try:
            result = XMLToolCallParser._parse_with_etree(content)
            if result and result.params:
                return result
            # params 为空但可能有散落的参数标签 → 继续尝试修复
        except ET.ParseError:
            pass

        # 修复 LLM 常见错误后重试 —— 每个 repair 实际改写输入时往 warnings 登记一条
        warnings: List[str] = []
        repaired = XMLToolCallParser._repair_tool_name_as_tag(content, warnings)
        repaired = XMLToolCallParser._repair_tag_equals_syntax(repaired, warnings)
        # 截断修复要先于 _repair_unclosed_cdata_tags：补 ]]> 后者才有可能 anchor
        repaired = XMLToolCallParser._repair_truncated_cdata(repaired, warnings)
        repaired = XMLToolCallParser._repair_unclosed_cdata_tags(repaired, warnings)
        # _repair_missing_closing_tags 要先于 _repair_scattered_params：
        # 否则只缺 </params> 时会被误判为 "params 散落"，触发不相干的 warning
        repaired = XMLToolCallParser._repair_missing_closing_tags(repaired, warnings)
        repaired = XMLToolCallParser._repair_scattered_params(repaired, warnings)

        result: Optional[ToolCall] = None
        if repaired != content:
            try:
                result = XMLToolCallParser._parse_with_etree(repaired)
            except ET.ParseError:
                pass

        # Fallback: 正则解析（处理 LLM 格式不严格的情况）
        if result is None:
            result = XMLToolCallParser._fallback_parse(repaired)

        if result is not None:
            result.warnings = warnings
            return result

        # 所有解析手段均失败 → 返回 error ToolCall
        # 保证 engine 知道 agent 尝试了 tool call，而非静默忽略
        return ToolCall(
            name="__malformed__",
            params={},
            error=(
                "Your tool call could not be parsed. Please use the correct format:\n"
                "<tool_call>\n"
                "<name>tool_name</name>\n"
                "<params>\n"
                "<param_name><![CDATA[value]]></param_name>\n"
                "</params>\n"
                "</tool_call>"
            ),
            warnings=warnings,
        )

    @staticmethod
    def _parse_with_etree(content: str) -> Optional[ToolCall]:
        """使用 ElementTree 解析"""
        # 包装成完整 XML
        xml_str = f"<root>{content}</root>"
        root = ET.fromstring(xml_str)

        # 提取 name
        name_elem = root.find('name')
        if name_elem is None:
            return None
        name = (name_elem.text or "").strip()
        if not name:
            return None

        # 提取 params
        params_elem = root.find('params')
        params = XMLToolCallParser._parse_element(params_elem) if params_elem is not None else {}

        return ToolCall(name=name, params=params)

    @staticmethod
    def _parse_element(elem: ET.Element) -> Dict[str, Any]:
        """递归解析 XML 元素为字典"""
        result = {}

        for child in elem:
            result[child.tag] = XMLToolCallParser._parse_value(child)

        return result

    @staticmethod
    def _parse_value(elem: ET.Element) -> str:
        """解析单个元素的值（保持原始字符串，类型转换由 BaseTool._coerce_params 处理）"""
        return (elem.text or "").strip()

    @staticmethod
    def _repair_tool_name_as_tag(content: str, warnings: List[str]) -> str:
        """
        修复工具名作为 XML 标签包裹 params 的格式

        LLM 有时会把工具名写成标签，例如：
            <web_fetch>
            <params>
                <url><![CDATA[...]]></url>
            </params>
        修复为：
            <name>web_fetch</name>
            <params>
                <url><![CDATA[...]]></url>
            </params>

        也处理有闭合标签的情况：<web_fetch>...</web_fetch>
        """
        # 已有 <name> 标签 → 无需修复
        if re.search(r'<name[\s>=]', content) or re.search(r'<name>', content):
            return content

        # 匹配首个标签（跳过空白）
        match = re.match(r'\s*<(\w+)>(.*)', content, re.DOTALL)
        if not match:
            return content

        tag_name = match.group(1)
        rest = match.group(2)

        # 如果首标签就是 params → 不是工具名，跳过
        if tag_name.lower() == 'params':
            return content

        # 移除对应的闭合标签（如果有）
        rest = re.sub(rf'</\s*{re.escape(tag_name)}\s*>\s*$', '', rest, flags=re.DOTALL)

        warnings.append(
            f"Wrote tool name as outer wrapping tag (e.g., <{tag_name}>...</{tag_name}>). "
            f"Correct form: <name>{tag_name}</name> with <params> as a sibling. "
            f"Do not wrap the call body with the tool name."
        )
        return f'<name>{tag_name}</name>\n{rest}'

    @staticmethod
    def _repair_tag_equals_syntax(content: str, warnings: List[str]) -> str:
        """
        修复 <tag=value</tag> → <tag>value</tag>

        小模型容易把 <tag>value</tag> 写成 <tag=value</tag>，例如：
            <name=call_subagent</name>
        修复为：
            <name>call_subagent</name>
        """
        new_content = re.sub(
            r'<(\w+)=([^<>]+)</\1>',
            r'<\1>\2</\1>',
            content,
        )
        if new_content != content:
            warnings.append(
                "Used '=' inside tag opening (e.g., <name=foo</name>). "
                "Correct form: <name>foo</name>. "
                "Never use '=' in tag openings — open with '>' and close with '</tag>'."
            )
        return new_content

    # 模型续写的标准指引：用 update_artifact 的 old_str/new_str 做 anchor 续写，
    # 而不是 retry create_artifact 或 rewrite_artifact（两者都会再次撞上 max_tokens）。
    _CONTINUE_WITH_UPDATE_ARTIFACT = (
        "To append the missing tail without truncating again, call "
        "update_artifact(id=<same id>, old_str=<a unique snippet from the end of the "
        "saved content>, new_str=<that same snippet + your continuation>). "
        "This writes only the continuation. "
        "Do NOT retry the same create_artifact call and do NOT use rewrite_artifact — "
        "both re-send the full content and will truncate again."
    )

    @staticmethod
    def _repair_truncated_cdata(content: str, warnings: List[str]) -> str:
        """
        修复 max_tokens 截断导致的尾部未闭合结构。

        典型场景（内网部署 3000 token 输出上限）：模型写到一半被切断，
        留下没有 ]]> 和/或 </tag> 的尾巴。现有 _repair_unclosed_cdata_tags
        需要 ]]> 后还有标签做锚点，截断尾部不满足 → 字段被静默丢弃。

        本修复用栈式扫描跳过 CDATA 区域，找出末尾未闭合的字段标签：
        - 案例 A: <tag><![CDATA[... （CDATA 未闭合）→ 补 ]]></tag>
        - 案例 B: <tag><![CDATA[...]]> （CDATA 闭了但 </tag> 缺失，
                  且尾部无后续标签）→ 补 </tag>
        - 案例 C: <tag>... （直接文本无 CDATA，未闭合）→ 补 </tag>

        排除 params / tool_call —— 这两个由 _repair_missing_closing_tags
        和外层 parse_tool_calls 的未闭合块处理逻辑负责。

        ── gate（避免误判 _repair_unclosed_cdata_tags 已经覆盖的场景）──
        像 <content><![CDATA[x]]><id>foo</id></params> 这种"漏写 </content>
        但后面还有 sibling 标签"的情况，并不是截断 —— 应该让
        _repair_unclosed_cdata_tags 把 </content> 插在正确位置。判定依据：
        case B/C 的未闭合字段标签必须是输入里的最后一个 tag 事件（之后再无
        push/pop），否则视为 mid-content abandonment 而非 tail truncation。
        """
        stack: List[tuple] = []  # 未闭合标签栈：(tag_name, open_start_pos)
        pos = 0
        cdata_open_at_eof = False
        cdata_wrapper: Optional[str] = None  # CDATA 截断时其外层 tag
        last_tag_event_pos = -1  # 最后一次见到 tag-shape 内容的位置

        tag_re = re.compile(r'<(/?)(\w+)\s*>')

        while pos < len(content):
            cdata_idx = content.find('<![CDATA[', pos)
            tag_match = tag_re.search(content, pos)
            tag_idx = tag_match.start() if tag_match else -1

            # 选最早的事件
            if cdata_idx != -1 and (tag_idx == -1 or cdata_idx < tag_idx):
                cdata_end = content.find(']]>', cdata_idx + 9)
                if cdata_end == -1:
                    # CDATA 未闭合 → 截断点
                    cdata_open_at_eof = True
                    cdata_wrapper = stack[-1][0] if stack else None
                    break
                pos = cdata_end + 3
                continue

            if tag_idx == -1:
                break

            # gate 判断需要：任何 tag 事件（push / 正常 pop / mismatched 忽略）
            # 都会更新 last_tag_event_pos，因为它们都意味着"开标签之后还有 tag 形态的内容"
            last_tag_event_pos = tag_idx
            is_close = tag_match.group(1) == '/'
            tag_name = tag_match.group(2)
            if is_close:
                if stack and stack[-1][0] == tag_name:
                    stack.pop()
                # 不匹配的关闭标签忽略（其他 repair 不应该产生这种状态）
            else:
                stack.append((tag_name, tag_idx))
            pos = tag_match.end()

        # 决策：先处理 CDATA 未闭合
        if cdata_open_at_eof:
            if cdata_wrapper and cdata_wrapper.lower() not in ('params', 'tool_call'):
                content = content.rstrip() + f']]></{cdata_wrapper}>'
                warnings.append(
                    f"Output appears truncated mid-content inside <{cdata_wrapper}> "
                    f"(likely max_tokens output limit hit). Partial content was saved "
                    f"with auto-closed CDATA. "
                    f"{XMLToolCallParser._CONTINUE_WITH_UPDATE_ARTIFACT}"
                )
                return content
            # CDATA 直接在 params 下，或栈空 —— 补 ]]> 让结构合法即可
            content = content.rstrip() + ']]>'
            warnings.append(
                "Output truncated inside CDATA with no clear wrapping field tag "
                "(likely max_tokens output limit hit). Auto-closed CDATA. "
                "Verify with read_artifact first; "
                f"{XMLToolCallParser._CONTINUE_WITH_UPDATE_ARTIFACT}"
            )
            return content

        # CDATA 都正常闭了，但还有未关闭的字段标签（B / C 案例）
        # gate: 仅在栈顶未闭合标签的 open 就是最后一个 tag 事件时才认为是截断，
        # 否则交给 _repair_unclosed_cdata_tags 在正确位置插闭合标签。
        field_stack = [(n, p) for n, p in stack if n.lower() not in ('params', 'tool_call')]
        if field_stack:
            topmost_name, topmost_pos = field_stack[-1]
            if topmost_pos == last_tag_event_pos:
                tags_str = ', '.join(f'</{n}>' for n, _ in reversed(field_stack))
                suffix = ''.join(f'</{n}>' for n, _ in reversed(field_stack))
                content = content.rstrip() + suffix
                warnings.append(
                    f"Output truncated before closing tag(s) {tags_str} "
                    f"(likely max_tokens output limit hit). Field(s) auto-closed. "
                    f"If content is incomplete: "
                    f"{XMLToolCallParser._CONTINUE_WITH_UPDATE_ARTIFACT}"
                )
                return content

        return content

    @staticmethod
    def _repair_unclosed_cdata_tags(content: str, warnings: List[str]) -> str:
        """
        修复 CDATA 后缺失闭合标签的问题

        LLM 生成长内容时容易漏掉 CDATA 之后的闭合标签，例如：
            <content><![CDATA[...long text...]]>
            </params>
        修复为：
            <content><![CDATA[...long text...]]></content>
            </params>
        """
        # 匹配 <tag><![CDATA[...]]> 后面不是 </tag> 的情况
        def _repair_match(m):
            tag = m.group(1)
            cdata = m.group(2)
            after = m.group(3)
            return f'<{tag}><![CDATA[{cdata}]]></{tag}>{after}'

        # CDATA 内容用 (?:(?!\]\]>).)* 匹配，防止跨越 ]]> 边界回溯
        new_content = re.sub(
            r'<(\w+)>\s*<!\[CDATA\[((?:(?!\]\]>).)*)\]\]>(?!\s*</\1>)(\s*<[/\w])',
            _repair_match,
            content,
            flags=re.DOTALL,
        )
        if new_content != content:
            warnings.append(
                "Wrote <tag><![CDATA[...]]> without the matching </tag> before the next sibling tag. "
                "Always close the field tag immediately after ']]>' (e.g., <content><![CDATA[...]]></content>). "
                "Do not let CDATA blocks bleed into the next param."
            )
        return new_content

    @staticmethod
    def _repair_scattered_params(content: str, warnings: List[str]) -> str:
        """
        修复参数散落在多处的问题：
        1. 多个 <params> 块（如第一个是垃圾纯文本、第二个才有结构化内容）
        2. 参数标签出现在 <params> 外面（作为 <name> 的兄弟标签）

        例如：
            <name>create_artifact</name>
            <params><![CDATA[content]]></params>
            <content_type><![CDATA[text/markdown]]></content_type>
            <id><![CDATA[xxx]]></id>
            <params>
              <content><![CDATA[...]]></content>
              <title><![CDATA[...]]></title>
            </params>
        修复为：
            <name>create_artifact</name>
            <params>
              <content_type><![CDATA[text/markdown]]></content_type>
              <id><![CDATA[xxx]]></id>
              <content><![CDATA[...]]></content>
              <title><![CDATA[...]]></title>
            </params>
        """
        # 找到所有 <params>...</params> 块
        params_blocks = list(re.finditer(
            r'<params\s*>(.*?)</params\s*>', content, re.DOTALL
        ))

        # 提取 <name>...</name>
        name_match = re.search(r'<name[^>]*>.*?</name>', content, re.DOTALL)
        if not name_match:
            return content

        # 移除 name 和所有 params 块，检查是否有孤立标签
        remainder = content
        remainder = re.sub(
            r'<name[^>]*>.*?</name>', '', remainder, count=1, flags=re.DOTALL
        )
        for block in params_blocks:
            remainder = remainder.replace(block.group(0), '', 1)

        has_orphans = bool(re.search(r'<\w+\s*>', remainder))

        # 如果只有一个 params 块且没有孤立标签，无需修复
        if len(params_blocks) <= 1 and not has_orphans:
            return content

        # 收集参数内容
        all_children = []

        # 先收集孤立标签（放前面，后面 params 块中的同名 key 会覆盖）
        if has_orphans:
            for m in re.finditer(r'(<(\w+)>.*?</\2>)', remainder, re.DOTALL):
                all_children.append(m.group(1).strip())

        # 再收集有子元素的 params 块内容（跳过纯文本/CDATA 的垃圾块）
        for block in params_blocks:
            inner = block.group(1).strip()
            if re.search(r'<\w+\s*>', inner):
                all_children.append(inner)

        if not all_children:
            return content

        merged = '\n'.join(all_children)
        warnings.append(
            "Parameter tags appeared outside <params> or in multiple <params> blocks. "
            "Wrap ALL parameters in a single <params>...</params> block. "
            "Do not duplicate <params> and do not place param tags as siblings of <name>."
        )
        return f"{name_match.group(0)}\n<params>\n{merged}\n</params>"

    @staticmethod
    def _repair_missing_closing_tags(content: str, warnings: List[str]) -> str:
        """
        修复缺失的结构性闭合标签

        LLM 有时会忘记写 </params> 闭合标签，例如：
            <name>create_artifact</name>
            <params>
                <id><![CDATA[task_plan]]></id>
                <content><![CDATA[...]]></content>
            （缺少 </params>）
        """
        if re.search(r'<params\s*>', content) and not re.search(r'</params\s*>', content):
            content = content.rstrip() + '\n</params>'
            warnings.append(
                "Missing </params> closing tag. "
                "Always close <params> explicitly before </tool_call>."
            )

        return content

    @staticmethod
    def _fallback_parse(content: str) -> Optional[ToolCall]:
        """
        Fallback：正则解析
        处理 LLM 生成的非严格 XML 格式
        """
        # 提取 name
        name_match = re.search(r'<name>\s*(.*?)\s*</name>', content, re.DOTALL)
        if not name_match:
            return None

        name = name_match.group(1).strip()

        # 提取 params（兼容缺失 </params> 的情况）
        params = {}
        params_match = re.search(r'<params>(.*?)(?:</params>|$)', content, re.DOTALL)

        if params_match:
            params_content = params_match.group(1)
            params = XMLToolCallParser._fallback_parse_params(params_content)

        return ToolCall(name=name, params=params)

    @staticmethod
    def _fallback_parse_params(params_content: str) -> Dict[str, Any]:
        """Fallback 解析参数内容"""
        params = {}

        # 匹配顶层参数标签
        # 支持: <tag>value</tag> 或 <tag><![CDATA[value]]></tag>
        tag_pattern = r'<(\w+)>(.*?)</\1>'

        for match in re.finditer(tag_pattern, params_content, re.DOTALL):
            tag_name = match.group(1)
            tag_content = match.group(2).strip()
            params[tag_name] = XMLToolCallParser._extract_cdata_or_text(tag_content)

        return params

    @staticmethod
    def _extract_cdata_or_text(text: str) -> str:
        """提取 CDATA 内容或普通文本"""
        text = text.strip()

        # 检查是否是 CDATA
        cdata_match = re.match(r'<!\[CDATA\[(.*?)\]\]>', text, re.DOTALL)
        if cdata_match:
            return cdata_match.group(1)

        return text


# 便捷函数（保持向后兼容）
def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析工具调用"""
    return XMLToolCallParser.parse_tool_calls(text)


if __name__ == "__main__":
    # 测试用例
    test_cases = [
        ("标准CDATA格式", """
<tool_call>
    <name>web_search</name>
    <params>
        <query><![CDATA[python async tutorial]]></query>
        <max_results><![CDATA[10]]></max_results>
    </params>
</tool_call>
"""),

        ("包含代码内容（CDATA）", """
<tool_call>
    <name>create_artifact</name>
    <params>
        <id><![CDATA[code_sample]]></id>
        <content><![CDATA[
def hello():
    if x < 10 and y > 5:
        print("Hello & World")
]]></content>
    </params>
</tool_call>
"""),

        ("旧格式（无CDATA，fallback）", """
<tool_call>
    <name>search</name>
    <params>
        <query>simple query</query>
        <count>5</count>
    </params>
</tool_call>
"""),

        ("标签等号语法 <name=value</name>", """
<tool_call>
<name=call_subagent</name>
<params>
<agent_name><![CDATA[research_agent]]></agent_name>
<instruction><![CDATA[research topic X across multiple sources]]></instruction>
  </params>
</tool_call>
"""),

        ("缺失</params>闭合标签", """
<tool_call>
    <name>create_artifact</name>
    <params>
        <id><![CDATA[task_plan]]></id>
        <content_type><![CDATA[text/markdown]]></content_type>
        <title><![CDATA[金融AI新闻稿件撰写任务计划]]></title>
        <content><![CDATA[# Task: 金融AI新闻稿件撰写

## 任务目标
撰写本月金融领域AI科技新闻稿件
]]></content>
</tool_call>
"""),

        ("重复params块+孤立参数+name等号语法", """
<tool_call>
<name=create_artifact</name>
<params><![CDATA[content]]></params>
<content_type><![CDATA[text/markdown]]></content_type>
<id><![CDATA[总结报告-研究背景与范围]]></id>
<params>
  <content><![CDATA[# 总结报告

## 一、研究背景
当今时代正经历前所未有的知识爆炸。]]></content>
  <id><![CDATA[总结报告 - 研究背景与范围]]></id>
  <title><![CDATA[总结报告 - 研究背景与范围]]></title>
</params>
</tool_call>
"""),

        ("孤立参数无params包裹", """
<tool_call>
<name>web_search</name>
<query><![CDATA[人工智能研究报告]]></query>
<max_results><![CDATA[5]]></max_results>
</tool_call>
"""),

        ("缺失</tool_call>闭合+name等号语法+孤立参数", """
<tool_call>
<name=create_artifact</name>
<params><![CDATA[content]]></params>
<content_type>text/markdown</content_type>
<id><![CDATA[总结报告 - 研究背景与范围]]></id>
<title><![CDATA[总结报告 - 研究背景与范围]]></title>
</params>
"""),

        ("工具名作为标签（无闭合）", """
<tool_call>
<web_fetch>
<params>
<url><![CDATA[https://k.sina.com.cn/article_7879922977_1d5ae152101901bba2.html]]></url>
<max_content_length><![CDATA[20000]]></max_content_length>
</params>
</tool_call>
"""),

        ("工具名作为标签（有闭合）", """
<tool_call>
<web_search>
<params>
<query><![CDATA[AI research 2024]]></query>
<max_results><![CDATA[5]]></max_results>
</params>
</web_search>
</tool_call>
"""),

        ("完全不可解析的 tool_call 块", """
<tool_call>
some random garbage that is not xml at all
</tool_call>
"""),
    ]

    print("=" * 70)
    print("XML解析器测试（CDATA版本）")
    print("=" * 70)

    for desc, test in test_cases:
        print(f"\n{'=' * 60}")
        print(f"测试: {desc}")
        print(f"{'=' * 60}")

        results = parse_tool_calls(test)
        for result in results:
            print(f"工具名: {result.name}")
            if result.error:
                print(f"错误: {result.error}")
            else:
                print(f"参数:")
                for k, v in result.params.items():
                    print(f"  {k}: {repr(v)}")

"""
XML工具调用解析器
使用标准 xml.etree.ElementTree，支持 CDATA
"""

import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ToolCall:
    """工具调用数据结构"""
    name: str
    params: Dict[str, Any]
    raw_text: str = ""


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
        """解析单个 tool_call 块"""
        # 先尝试标准 XML 解析
        try:
            result = XMLToolCallParser._parse_with_etree(content)
            if result and result.params:
                return result
            # params 为空但可能有散落的参数标签 → 继续尝试修复
        except ET.ParseError:
            pass

        # 修复 LLM 常见错误后重试
        repaired = XMLToolCallParser._repair_tag_equals_syntax(content)
        repaired = XMLToolCallParser._repair_unclosed_cdata_tags(repaired)
        repaired = XMLToolCallParser._repair_scattered_params(repaired)
        repaired = XMLToolCallParser._repair_missing_closing_tags(repaired)
        if repaired != content:
            try:
                return XMLToolCallParser._parse_with_etree(repaired)
            except ET.ParseError:
                pass

        # Fallback: 正则解析（处理 LLM 格式不严格的情况）
        return XMLToolCallParser._fallback_parse(repaired)

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
            tag = child.tag

            # 检查是否有子元素（列表情况）
            if len(child) > 0:
                # 有子元素 → 解析为列表
                items = [XMLToolCallParser._parse_value(item) for item in child]
                result[tag] = items
            else:
                # 无子元素 → 解析值
                result[tag] = XMLToolCallParser._parse_value(child)

        return result

    @staticmethod
    def _parse_value(elem: ET.Element) -> Any:
        """解析单个元素的值，处理类型转换"""
        text = (elem.text or "").strip()
        return XMLToolCallParser._convert_type(text)

    @staticmethod
    def _convert_type(text: str) -> Any:
        """将字符串转换为合适的类型"""
        if not text:
            return text

        # 布尔值
        if text.lower() == 'true':
            return True
        if text.lower() == 'false':
            return False

        # 数字
        try:
            if '.' in text or 'e' in text.lower():
                return float(text)
            return int(text)
        except ValueError:
            pass

        return text

    @staticmethod
    def _repair_tag_equals_syntax(content: str) -> str:
        """
        修复 <tag=value</tag> → <tag>value</tag>

        小模型容易把 <tag>value</tag> 写成 <tag=value</tag>，例如：
            <name=call_subagent</name>
        修复为：
            <name>call_subagent</name>
        """
        return re.sub(
            r'<(\w+)=([^<>]+)</\1>',
            r'<\1>\2</\1>',
            content,
        )

    @staticmethod
    def _repair_unclosed_cdata_tags(content: str) -> str:
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
        return re.sub(
            r'<(\w+)>\s*<!\[CDATA\[((?:(?!\]\]>).)*)\]\]>(?!\s*</\1>)(\s*<[/\w])',
            _repair_match,
            content,
            flags=re.DOTALL,
        )

    @staticmethod
    def _repair_scattered_params(content: str) -> str:
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
        return f"{name_match.group(0)}\n<params>\n{merged}\n</params>"

    @staticmethod
    def _repair_missing_closing_tags(content: str) -> str:
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

            # 检查是否包含子元素（列表）
            if re.search(r'<\w+>', tag_content):
                # 解析为列表
                items = []
                for item_match in re.finditer(r'<\w+>(.*?)</\w+>', tag_content, re.DOTALL):
                    item_value = XMLToolCallParser._extract_cdata_or_text(item_match.group(1))
                    items.append(XMLToolCallParser._convert_type(item_value))
                params[tag_name] = items
            else:
                # 普通值
                value = XMLToolCallParser._extract_cdata_or_text(tag_content)
                params[tag_name] = XMLToolCallParser._convert_type(value)

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


# 向后兼容别名
SimpleXMLParser = XMLToolCallParser


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

        ("列表参数（CDATA）", """
<tool_call>
    <name>web_fetch</name>
    <params>
        <url_list>
            <item><![CDATA[https://example.com?a=1&b=2]]></item>
            <item><![CDATA[https://test.com]]></item>
        </url_list>
        <timeout><![CDATA[30]]></timeout>
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
<agent_name><![CDATA[crawl_agent]]></agent_name>
<instruction><![CDATA[爬取文章内容]]></instruction>
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
            print(f"参数:")
            for k, v in result.params.items():
                print(f"  {k}: {repr(v)}")

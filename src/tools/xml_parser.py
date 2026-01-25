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

        return results

    @staticmethod
    def _parse_single_block(content: str) -> Optional[ToolCall]:
        """解析单个 tool_call 块"""
        # 先尝试标准 XML 解析
        try:
            return XMLToolCallParser._parse_with_etree(content)
        except ET.ParseError:
            pass

        # Fallback: 正则解析（处理 LLM 格式不严格的情况）
        return XMLToolCallParser._fallback_parse(content)

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

        # 提取 params
        params = {}
        params_match = re.search(r'<params>(.*?)</params>', content, re.DOTALL)

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

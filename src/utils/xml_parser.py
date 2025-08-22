"""
Robust XML解析器
核心功能：
- 标准XML解析
- 自动修复常见错误
- 降级到正则提取
- 专门处理Agent工具调用格式
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass


@dataclass
class ToolCall:
    """工具调用数据结构"""
    name: str
    params: Dict[str, Any]
    raw_text: str = ""  # 保留原始文本用于调试


class RobustXMLParser:
    """健壮的XML解析器，专门处理LLM输出"""
    
    @staticmethod
    def parse_tool_calls(text: str) -> List[ToolCall]:
        """
        解析工具调用XML
        期望格式:
        <tool_call>
            <name>web_search</name>
            <params>
                <query>AI medical FDA approval</query>
            </params>
        </tool_call>
        """
        tool_calls = []
        
        # 方法1: 标准XML解析
        try:
            # 先尝试提取所有tool_call块
            tool_blocks = re.findall(
                r'<tool_call>.*?</tool_call>', 
                text, 
                re.DOTALL | re.IGNORECASE
            )
            
            for block in tool_blocks:
                tool_call = RobustXMLParser._parse_single_tool_call(block)
                if tool_call:
                    tool_calls.append(tool_call)
            
            if tool_calls:
                return tool_calls
                
        except Exception:
            pass
        
        # 方法2: 修复后解析
        fixed_text = RobustXMLParser._fix_common_errors(text)
        if fixed_text != text:
            try:
                return RobustXMLParser.parse_tool_calls(fixed_text)
            except Exception:
                pass
        
        # 方法3: 正则提取（最后的降级方案）
        tool_calls_regex = RobustXMLParser._extract_with_regex(text)
        if tool_calls_regex:
            return tool_calls_regex
        
        return []
    
    @staticmethod
    def _parse_single_tool_call(xml_text: str) -> Optional[ToolCall]:
        """解析单个tool_call块"""
        try:
            root = ET.fromstring(xml_text)
            
            # 提取name
            name_elem = root.find('name')
            if name_elem is None:
                return None
            name = name_elem.text.strip()
            
            # 提取params
            params = {}
            params_elem = root.find('params')
            if params_elem is not None:
                for param in params_elem:
                    key = param.tag
                    value = param.text
                    if value:
                        # 尝试解析为合适的类型
                        value = RobustXMLParser._parse_value(value.strip())
                    params[key] = value
            
            return ToolCall(name=name, params=params, raw_text=xml_text)
            
        except ET.ParseError:
            return None
    
    @staticmethod
    def _fix_common_errors(text: str) -> str:
        """修复常见的XML错误"""
        fixed = text
        
        # 1. 修复未闭合的标签
        # 查找所有开标签
        open_tags = re.findall(r'<(\w+)>', fixed)
        for tag in open_tags:
            # 检查是否有对应的闭标签
            if f'</{tag}>' not in fixed:
                # 在下一个开标签或文本末尾前添加闭标签
                pattern = f'<{tag}>(.*?)(?=<|$)'
                fixed = re.sub(pattern, f'<{tag}>\\1</{tag}>', fixed, flags=re.DOTALL)
        
        # 2. 转义特殊字符（在标签内容中）
        def escape_content(match):
            content = match.group(1)
            # 只转义标签内容中的特殊字符，不转义标签本身
            content = content.replace('&', '&amp;')
            content = re.sub(r'<(?!/|[a-zA-Z])', '&lt;', content)
            content = re.sub(r'(?<![a-zA-Z/])>', '&gt;', content)
            return f'>{content}<'
        
        # 只处理标签之间的内容
        fixed = re.sub(r'>([^<]+)<', escape_content, fixed)
        
        # 3. 修复错误的引号（如果在属性中）
        fixed = re.sub(r'([a-zA-Z]+)=(["\'])([^"\']*)\2', r'\1="\3"', fixed)
        
        return fixed
    
    @staticmethod
    def _extract_with_regex(text: str) -> List[ToolCall]:
        """使用正则表达式提取（降级方案）"""
        tool_calls = []
        
        # 尝试多种模式
        patterns = [
            # 标准格式
            r'<tool_call>.*?<name>(.*?)</name>.*?<params>(.*?)</params>.*?</tool_call>',
            # 可能缺少params标签
            r'<tool_call>.*?<name>(.*?)</name>(.*?)</tool_call>',
            # 更宽松的格式
            r'tool[_\s]?call:?\s*(\w+).*?params?:?\s*\{([^}]+)\}',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    name = match[0].strip()
                    params_text = match[1] if len(match) > 1 else ""
                    
                    # 解析params
                    params = RobustXMLParser._parse_params_text(params_text)
                    
                    if name:
                        tool_calls.append(ToolCall(
                            name=name,
                            params=params,
                            raw_text=str(match)
                        ))
        
        return tool_calls
    
    @staticmethod
    def _parse_params_text(text: str) -> Dict[str, Any]:
        """从文本中解析参数"""
        params = {}
        
        # 尝试XML格式 <key>value</key>
        xml_params = re.findall(r'<(\w+)>(.*?)</\1>', text, re.DOTALL)
        for key, value in xml_params:
            params[key] = RobustXMLParser._parse_value(value.strip())
        
        # 如果没有找到XML格式，尝试key:value或key=value格式
        if not params:
            # key: value 格式
            kv_params = re.findall(r'(\w+)\s*[:=]\s*([^,\n]+)', text)
            for key, value in kv_params:
                value = value.strip().strip('"\'')
                params[key] = RobustXMLParser._parse_value(value)
        
        return params
    
    @staticmethod
    def _parse_value(value: str) -> Any:
        """尝试将字符串解析为合适的类型"""
        if not value:
            return value
        
        # 布尔值
        if value.lower() == 'true':
            return True
        if value.lower() == 'false':
            return False
        
        # 数字
        try:
            if '.' in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        
        # 列表（简单判断）
        if value.startswith('[') and value.endswith(']'):
            try:
                # 简单的列表解析
                items = value[1:-1].split(',')
                return [item.strip().strip('"\'') for item in items]
            except:
                pass
        
        # 默认返回字符串
        return value
    
    @staticmethod
    def extract_tag_content(text: str, tag: str) -> Optional[str]:
        """提取指定标签的内容（通用方法）"""
        # 先尝试XML解析
        try:
            # 包装成完整的XML
            wrapped = f'<root>{text}</root>'
            root = ET.fromstring(wrapped)
            elem = root.find(f'.//{tag}')
            if elem is not None:
                return elem.text
        except:
            pass
        
        # 降级到正则
        pattern = f'<{tag}>(.*?)</{tag}>'
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        return None


# 便捷函数
def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析工具调用的便捷函数"""
    return RobustXMLParser.parse_tool_calls(text)


def extract_tag(text: str, tag: str) -> Optional[str]:
    """提取标签内容的便捷函数"""
    return RobustXMLParser.extract_tag_content(text, tag)


if __name__ == "__main__":
    # 测试代码
    test_cases = [
        # 正常格式
        """
        <tool_call>
            <name>web_search</name>
            <params>
                <query>AI medical FDA approval</query>
                <limit>10</limit>
            </params>
        </tool_call>
        """,
        
        # 缺少闭标签
        """
        <tool_call>
            <name>web_search
            <params>
                <query>test query</query>
        </tool_call>
        """,
        
        # 特殊字符
        """
        <tool_call>
            <name>web_fetch</name>
            <params>
                <url>https://example.com?a=1&b=2</url>
            </params>
        </tool_call>
        """,
        
        # 多个调用
        """
        <tool_call>
            <name>search</name>
            <params><query>first</query></params>
        </tool_call>
        
        <tool_call>
            <name>fetch</name>
            <params><url>http://test.com</url></params>
        </tool_call>
        """
    ]
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n测试用例 {i}:")
        print("> 输入:", test[:100] + "..." if len(test) > 100 else test)
        
        results = parse_tool_calls(test)
        for result in results:
            print(f"> 工具: {result.name}")
            print(f"> 参数: {result.params}")
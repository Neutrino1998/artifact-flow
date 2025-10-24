"""
简化版XML解析器 - 重构版
统一的顶层标签提取逻辑
"""

import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass


@dataclass
class ToolCall:
    """工具调用数据结构"""
    name: str
    params: Dict[str, Any]
    raw_text: str = ""


class SimpleXMLParser:
    """纯正则的简化XML解析器"""
    
    # 标签名规则：字母/下划线开头，可含字母数字下划线连字符，1-20字符
    TAG_PATTERN = r'[a-zA-Z_][\w\-]{0,19}'
    
    @staticmethod
    def parse_tool_calls(text: str) -> List[ToolCall]:
        """解析所有tool_call"""
        tool_calls = []
        
        # 提取所有tool_call块
        tool_blocks = SimpleXMLParser._extract_blocks(text, 'tool_call')
        
        # 解析每个块
        for content, raw in tool_blocks:
            tool_call = SimpleXMLParser._parse_single_tool_call(content)
            if tool_call:
                tool_call.raw_text = raw
                tool_calls.append(tool_call)
        
        return tool_calls
    
    @staticmethod
    def _extract_blocks(text: str, tag: str) -> List[Tuple[str, str]]:
        """
        提取指定标签的所有块
        返回: [(内容, 原始块), ...]
        """
        results = []
        pos = 0
        
        while pos < len(text):
            # 查找下一个开标签
            open_pattern = f'<{tag}>'
            open_match = re.search(open_pattern, text[pos:], re.IGNORECASE)
            if not open_match:
                break
            
            block_start = pos + open_match.start()
            content_start = pos + open_match.end()
            
            # 查找对应的闭标签
            close_pattern = f'</{tag}>'
            close_match = re.search(close_pattern, text[content_start:], re.IGNORECASE)
            
            if close_match:
                content_end = content_start + close_match.start()
                block_end = content_start + close_match.end()
                content = text[content_start:content_end]
                raw = text[block_start:block_end]
                pos = block_end
            else:
                # 没有闭标签
                next_open = re.search(open_pattern, text[content_start:], re.IGNORECASE)
                if next_open:
                    content_end = content_start + next_open.start()
                else:
                    content_end = len(text)
                
                content = text[content_start:content_end].rstrip()
                raw = text[block_start:content_end]
                pos = content_end
            
            results.append((content, raw))
        
        return results
    
    @staticmethod
    def _extract_sibling_tags(text: str) -> List[Tuple[str, str]]:
        """
        提取文本中所有的顶层（同级）标签
        
        核心策略：
        1. 顺序扫描，找到每个开标签
        2. 对每个标签，找闭标签或通过缩进判断边界
        3. 返回 [(标签名, 内容), ...]
        
        这是整个解析器的核心方法，适用于：
        - params 中提取参数标签
        - list 中提取子元素标签
        """
        results = []
        pos = 0
        
        while pos < len(text):
            # 查找下一个开标签
            tag_pattern = f'<({SimpleXMLParser.TAG_PATTERN})>'
            match = re.search(tag_pattern, text[pos:], re.IGNORECASE)
            
            if not match:
                break
            
            tag_name = match.group(1)
            tag_start = pos + match.start()
            content_start = pos + match.end()
            
            # 查找对应的闭标签
            close_pattern = f'</{re.escape(tag_name)}>'
            close_match = re.search(close_pattern, text[content_start:], re.IGNORECASE)
            
            if close_match:
                # 找到闭标签 - 完整配对
                content_end = content_start + close_match.start()
                block_end = content_start + close_match.end()
                tag_content = text[content_start:content_end]
                pos = block_end
            else:
                # 没有闭标签 - 使用缩进判断边界
                content_end = SimpleXMLParser._find_boundary_by_indent(
                    text, content_start, tag_start
                )
                tag_content = text[content_start:content_end].rstrip()
                pos = content_end
            
            results.append((tag_name, tag_content))
        
        return results
    
    @staticmethod
    def _parse_single_tool_call(content: str) -> Optional[ToolCall]:
        """解析单个tool_call块的内容"""
        # 提取name - 简单标签，只取第一行
        name = SimpleXMLParser._extract_simple_tag(content, 'name')
        if not name:
            return None
        
        # 提取params内容
        params_blocks = SimpleXMLParser._extract_blocks(content, 'params')
        if params_blocks:
            params = SimpleXMLParser._parse_params(params_blocks[0][0])
        else:
            params = {}
        
        return ToolCall(name=name, params=params)
    
    @staticmethod
    def _extract_simple_tag(text: str, tag: str) -> Optional[str]:
        """
        提取简单标签（如name）的内容
        简单标签的特点：内容通常是单行的简短文本
        如果没有闭标签，只取第一行非空内容
        """
        open_pattern = f'<{tag}>'
        open_match = re.search(open_pattern, text, re.IGNORECASE)
        if not open_match:
            return None
        
        content_start = open_match.end()
        
        # 查找闭标签
        close_pattern = f'</{tag}>'
        close_match = re.search(close_pattern, text[content_start:], re.IGNORECASE)
        
        if close_match:
            return text[content_start:content_start + close_match.start()].strip()
        else:
            # 没有闭标签，取第一行非空内容
            remaining = text[content_start:].lstrip()
            
            # 找到第一个换行符或下一个标签
            newline_pos = remaining.find('\n')
            next_tag = re.search(f'<{SimpleXMLParser.TAG_PATTERN}>', remaining)
            next_tag_pos = next_tag.start() if next_tag else len(remaining)
            
            if newline_pos != -1:
                end_pos = min(newline_pos, next_tag_pos)
            else:
                end_pos = next_tag_pos
            
            return remaining[:end_pos].strip()
    
    @staticmethod
    def _parse_params(params_content: str) -> Dict[str, Any]:
        """
        解析params内容
        使用统一的 _extract_sibling_tags 提取所有参数
        """
        params = {}
        
        # 提取所有顶层参数标签
        sibling_tags = SimpleXMLParser._extract_sibling_tags(params_content)
        
        for param_name, param_content in sibling_tags:
            # 判断是否是list类型参数
            if 'list' in param_name.lower():
                params[param_name] = SimpleXMLParser._extract_list_items(param_content)
            else:
                params[param_name] = SimpleXMLParser._parse_value(param_content)
        
        return params
    
    @staticmethod
    def _extract_list_items(content: str) -> List[Any]:
        """
        提取列表中的所有项
        使用统一的 _extract_sibling_tags 提取所有子元素
        """
        items = []
        
        # 提取所有子标签
        sibling_tags = SimpleXMLParser._extract_sibling_tags(content)
        
        for tag_name, tag_content in sibling_tags:
            items.append(SimpleXMLParser._parse_value(tag_content))
        
        # 如果没找到子标签，尝试解析旧格式JSON数组
        if not items and content.strip():
            content_stripped = content.strip()
            if content_stripped.startswith('[') and content_stripped.endswith(']'):
                try:
                    items_str = content_stripped[1:-1].split(',')
                    items = [s.strip().strip('"\'') for s in items_str if s.strip()]
                except:
                    pass
        
        return items
    
    @staticmethod
    def _find_boundary_by_indent(text: str, start: int, tag_start: int) -> int:
        """
        通过缩进判断标签内容边界
        
        返回下一个"缩进 ≤ 当前标签缩进"的标签位置，或文本结束
        这样确保嵌套的子标签不会被误认为同级标签
        
        参数：
        - text: 完整文本
        - start: 内容开始位置（标签闭合符>之后）
        - tag_start: 标签开始位置（<标签名）
        """
        # 获取当前标签的缩进
        line_start = text.rfind('\n', 0, tag_start)
        if line_start == -1:
            line_start = 0
        else:
            line_start += 1
        
        current_indent = tag_start - line_start
        
        # 从start开始查找下一个同级或外层标签
        pos = start
        tag_pattern = f'<({SimpleXMLParser.TAG_PATTERN})>'
        
        while pos < len(text):
            match = re.search(tag_pattern, text[pos:], re.IGNORECASE)
            if not match:
                # 没有更多标签，返回文本结束
                return len(text)
            
            tag_pos = pos + match.start()
            
            # 获取这个标签的缩进
            line_start = text.rfind('\n', 0, tag_pos)
            if line_start == -1:
                line_start = 0
            else:
                line_start += 1
            
            tag_indent = tag_pos - line_start
            
            # 缩进小于等于当前标签，说明是同级或外层，这就是边界
            if tag_indent <= current_indent:
                return tag_pos
            
            # 否则是嵌套标签，继续往后找
            pos = tag_pos + 1
        
        return len(text)
    
    @staticmethod
    def _parse_value(value: str) -> Any:
        """将字符串转换为合适的类型"""
        if not value:
            return value
        
        value = value.strip()
        
        # 布尔值
        if value.lower() == 'true':
            return True
        if value.lower() == 'false':
            return False
        
        # 数字
        try:
            # 包含小数点或科学计数法标记（e/E）→ float
            if '.' in value or 'e' in value.lower():
                return float(value)
            # 纯整数 → int
            return int(value)
        except ValueError:
            pass
        
        return value


# 便捷函数
def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析工具调用"""
    return SimpleXMLParser.parse_tool_calls(text)


if __name__ == "__main__":
    # 测试用例
    test_cases = [
        ("标准格式", """
<tool_call>
    <name>web_search</name>
    <params>
        <query>test query</query>
    </params>
</tool_call>
"""),
        
        ("list参数，包含特殊字符", """
<tool_call>
    <name>web_fetch</name>
    <params>
        <url_list>
            <item>https://example.com?a=1&b=2</item>
            <item>https://test.com</item>
        </url_list>
        <timeout>30</timeout>
    </params>
</tool_call>
"""),
        
        ("包含复杂代码-未闭合content标签", """
<tool_call>
    <name>create_artifact</name>
    <params>
        <id>research_report</id>
        <content_type>markdown</content_type>
        <title>LangGraph Analysis Report</title>
        <content>
Advanced Usage
Conditional Edges
def should_continue(state: State):
    if state["count"] < 3:
        return "a"
    else:
        return END
workflow.add_conditional_edges(
    "b",
    should_continue,
    {
        "a": "a",
        END: END
    }
)
        </params>
</tool_call>
"""),
        
        ("多个tool_call", """
<tool_call>
    <name>search</name>
    <params><query>first</query></params>
</tool_call>

<tool_call>
    <name>fetch</name>
    <params>
        <url_list>
            <url>http://a.com</url>
            <url>http://b.com</url>
        </url_list>
    </params>
</tool_call>
"""),
        
        ("包含简单XML内容", """
<tool_call>
    <name>create_artifact</name>
    <params>
        <artifact_type>application/xml</artifact_type>
        <content>
<config>
    <database>
        <host>localhost</host>
        <port>5432</port>
    </database>
    <cache>
        <enabled>true</enabled>
    </cache>
</config>
        </content>
    </params>
</tool_call>
"""),
    ]
    
    print("="*70)
    print("XML解析器测试")
    print("="*70)
    
    for i, (desc, test) in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"测试用例 {i}: {desc}")
        print(f"{'='*60}")
        print(test.strip())
        print(f"\n{'结果':->40}")
        
        results = parse_tool_calls(test)
        for result in results:
            print(f"✓ 工具名: {result.name}")
            if result.params:
                print(f"  参数:")
                for k, v in result.params.items():
                    print(f"    • {k}: {v}")
            else:
                print(f"  参数: (无)")
            print()
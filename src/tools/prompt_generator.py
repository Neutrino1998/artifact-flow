"""
XML提示词生成器
为Agent生成工具调用的提示词和指令
"""

from typing import List, Optional, Dict, Any
from tools.base import BaseTool, ToolParameter


class ToolPromptGenerator:
    """
    工具提示词生成器
    生成Agent调用工具所需的XML格式提示
    """
    
    @staticmethod
    def generate_tool_instruction(tools: List[BaseTool]) -> str:
        """
        生成工具使用说明
        
        Args:
            tools: 工具列表
            
        Returns:
            工具使用说明文本
        """
        if not tools:
            return "<tool_instructions>\nNo tools available.\n</tool_instructions>"
        
        instruction = """<tool_instructions>
You have access to the following tools. To use a tool, format your request in XML:

<tool_call>
  <name>tool_name</name>
  <params>
    <param_name>param_value</param_name>
  </params>
</tool_call>

Available tools:
"""
        
        # 添加每个工具的说明
        for tool in tools:
            instruction += f"\n{ToolPromptGenerator._format_tool_doc(tool)}"
        
        instruction += """

Important guidelines:
1. Always use the exact tool name as specified
2. Include all required parameters
3. Use proper XML formatting with closed tags
4. You can make multiple tool calls in sequence
5. Wait for tool results before proceeding with analysis
</tool_instructions>"""
        
        return instruction
    
    @staticmethod
    def _format_tool_doc(tool: BaseTool) -> str:
        """
        格式化单个工具的文档
        
        Args:
            tool: 工具实例
            
        Returns:
            工具文档字符串
        """
        doc = f"### {tool.name}\n"
        doc += f"Description: {tool.description}\n"
        
        # 参数说明
        params = tool.get_parameters()
        if params:
            doc += "Parameters:\n"
            for param in params:
                required = " (required)" if param.required else " (optional)"
                doc += f"  - {param.name}: {param.type}{required} - {param.description}\n"
                if param.default is not None:
                    doc += f"    Default: {param.default}\n"
        else:
            doc += "Parameters: None\n"
        
        # 添加示例
        doc += f"Example:\n{tool.to_xml_example()}\n"
        
        return doc
    
    @staticmethod
    def generate_tool_response_format() -> str:
        """
        生成工具响应格式说明
        
        Returns:
            响应格式说明
        """
        return """Tool results will be provided in the following format:

<tool_result>
  <name>tool_name</name>
  <success>true/false</success>
  <data>
    <!-- Result data here -->
  </data>
  <error><!-- Error message if failed --></error>
</tool_result>

After receiving tool results, analyze them and continue with your task."""
    
    @staticmethod
    def format_tool_result(name: str, result: Dict[str, Any]) -> str:
        """
        格式化工具执行结果为XML
        
        Args:
            name: 工具名称
            result: 执行结果
            
        Returns:
            XML格式的结果
        """
        success = result.get("success", False)
        data = result.get("data", "")
        error = result.get("error", "")
        
        # 处理data的格式化
        if isinstance(data, dict):
            data_str = ToolPromptGenerator._dict_to_xml(data)
        elif isinstance(data, list):
            data_str = ToolPromptGenerator._list_to_xml(data)
        else:
            data_str = str(data) if data else ""
        
        xml = f"""<tool_result>
  <name>{name}</name>
  <success>{'true' if success else 'false'}</success>"""
        
        if data_str:
            xml += f"\n  <data>\n{ToolPromptGenerator._indent(data_str, 4)}\n  </data>"
        
        if error:
            xml += f"\n  <error>{error}</error>"
        
        xml += "\n</tool_result>"
        
        return xml
    
    @staticmethod
    def _dict_to_xml(data: Dict, indent: int = 0) -> str:
        """
        字典转XML
        
        Args:
            data: 字典数据
            indent: 缩进级别
            
        Returns:
            XML字符串
        """
        lines = []
        for key, value in data.items():
            # 清理key（XML标签名不能有空格等）
            key = key.replace(" ", "_").replace("-", "_")
            
            if isinstance(value, dict):
                lines.append(f"<{key}>")
                lines.append(ToolPromptGenerator._dict_to_xml(value, indent + 2))
                lines.append(f"</{key}>")
            elif isinstance(value, list):
                lines.append(f"<{key}>")
                lines.append(ToolPromptGenerator._list_to_xml(value, indent + 2))
                lines.append(f"</{key}>")
            else:
                lines.append(f"<{key}>{value}</{key}>")
        
        return "\n".join(lines)
    
    @staticmethod
    def _list_to_xml(data: List, indent: int = 0) -> str:
        """
        列表转XML
        
        Args:
            data: 列表数据
            indent: 缩进级别
            
        Returns:
            XML字符串
        """
        lines = []
        for item in data:
            if isinstance(item, dict):
                lines.append("<item>")
                lines.append(ToolPromptGenerator._dict_to_xml(item, indent + 2))
                lines.append("</item>")
            else:
                lines.append(f"<item>{item}</item>")
        
        return "\n".join(lines)
    
    @staticmethod
    def _indent(text: str, spaces: int) -> str:
        """
        为文本添加缩进
        
        Args:
            text: 原始文本
            spaces: 缩进空格数
            
        Returns:
            缩进后的文本
        """
        indent_str = " " * spaces
        return "\n".join(indent_str + line for line in text.split("\n"))

# 便捷函数
def generate_tool_prompt(tools: List[BaseTool]) -> str:
    """生成工具提示词的便捷函数"""
    return ToolPromptGenerator.generate_tool_instruction(tools)


def format_result(name: str, result: Dict[str, Any]) -> str:
    """格式化工具结果的便捷函数"""
    return ToolPromptGenerator.format_tool_result(name, result)
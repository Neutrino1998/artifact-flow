"""
XML 格式化器
将工具定义和执行结果序列化为 XML 文本（供 LLM system prompt / context 使用）
与 xml_parser.py 互为 formatter / parser 对
"""

from typing import List, Dict, Any

from tools.base import BaseTool


def generate_tool_instruction(tools: List[BaseTool]) -> str:
    """生成工具使用说明（注入 system prompt）"""
    if not tools:
        return "<tool_instructions>\nNo tools available.\n</tool_instructions>"

    instruction = """<tool_instructions>
<format>
You may make one or more tool calls per turn. They execute sequentially.
Wrap ALL parameter values in <![CDATA[...]]>.

For multiple calls, emit each <tool_call> block one after another — there is NO wrapping container tag:
<tool_call>
  <name>first_tool</name>
  <params>...</params>
</tool_call>
<tool_call>
  <name>second_tool</name>
  <params>...</params>
</tool_call>
</format>
"""

    for tool in tools:
        instruction += f"\n{_format_tool_doc(tool)}"

    instruction += "\n</tool_instructions>"

    return instruction


def format_result(name: str, result: Dict[str, Any]) -> str:
    """格式化工具执行结果为 XML（注入 context 消息）

    parser_warnings 在 <data> 前作为独立子节点渲染（兜底修复/截断提示），
    保证模型在下一轮 context 里看到 "这次解析做了什么、你下次应该怎么写"。
    """
    success = result.get("success", False)
    data = result.get("data", "")
    error = result.get("error", "")
    parser_warnings = result.get("parser_warnings") or []

    xml = f'<tool_result name="{name}" success="{"true" if success else "false"}">'

    if parser_warnings:
        warnings_body = "\n".join(f"- {w}" for w in parser_warnings)
        xml += f"\n<parser_warnings>\n{warnings_body}\n</parser_warnings>"

    if data:
        xml += f"\n<data>\n{data}\n</data>"

    if error:
        xml += f"\n  <error>{error}</error>"

    xml += "\n</tool_result>"

    return xml


def _format_tool_doc(tool: BaseTool) -> str:
    """格式化单个工具的文档"""
    doc = f'<tool name="{tool.name}">\n'
    doc += f"{tool.description}\n"

    params = tool.get_parameters()
    if params:
        doc += "Parameters:\n"
        for param in params:
            required = " (required)" if param.required else " (optional)"
            doc += f"  - {param.name}: {param.type}{required} - {param.description}\n"
            if param.enum:
                doc += f"    Values: {', '.join(param.enum)}\n"
            if param.default is not None:
                doc += f"    Default: {param.default}\n"
    else:
        doc += "Parameters: None\n"

    if tool.show_example:
        doc += f"Example:\n{tool.to_xml_example()}\n"

    doc += "</tool>"

    return doc

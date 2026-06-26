"""
XML 格式化器
将工具定义和执行结果序列化为 XML 文本（供 LLM system prompt / context 使用）
与 xml_parser.py 互为 formatter / parser 对
"""

from typing import List, Dict, Any

from tools.base import BaseTool


# 工具调用协议语法块 —— 对所有 agent / 所有轮恒等,是理想的 prompt-cache 可缓存前缀。
# per-tool 描述不在此(B-3 渐进式披露):描述挪到 <available_tools> 动态 reminder,
# 故 catalog 变化只失效消息尾部、这段语法前缀恒稳。
_TOOL_GRAMMAR_BODY = """<format>
You may make one or more tool calls per turn. They execute sequentially.
Wrap ALL parameter values in <![CDATA[...]]>.

Every tool call must include a <reason> sibling before <name>: one short sentence, in the user's language, saying why you are making THIS call. It is shown to the user (and is what they read when a tool needs their approval). <reason> is NOT a parameter — it goes outside <params>, never inside it.

<tool_call>
  <reason><![CDATA[why you are making this call]]></reason>
  <name>tool_name</name>
  <params>...</params>
</tool_call>

For multiple calls, emit each <tool_call> block one after another — there is NO wrapping container tag:
<tool_call>
  <reason><![CDATA[why the first call]]></reason>
  <name>first_tool</name>
  <params>...</params>
</tool_call>
<tool_call>
  <reason><![CDATA[why the second call]]></reason>
  <name>second_tool</name>
  <params>...</params>
</tool_call>
</format>"""


def generate_tool_grammar() -> str:
    """工具调用协议语法块（注入 system prompt 稳定前缀，保 APC）。

    不含任何 per-tool 描述 —— 描述挪到 `<available_tools>` 动态 reminder（B-3 渐进式
    披露，见 ContextManager._build_available_tools）。语法对所有 agent / 所有轮恒等。
    """
    return f"<tool_instructions>\n{_TOOL_GRAMMAR_BODY}\n</tool_instructions>"


def render_tool_docs(tools: List[BaseTool]) -> str:
    """渲染一组工具的完整 doc（name/description/params/example），换行连接。

    供 `<available_tools>` 的 non-deferred 段与 `search_tools` 结果共用 —— 「完整描述
    长什么样」只此一处定义,两条披露路径不会漂移。
    """
    return "\n".join(_format_tool_doc(tool) for tool in tools)


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

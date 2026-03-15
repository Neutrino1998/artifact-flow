"""
工具系统模块
提供工具基类、XML解析/格式化和具体工具实现
"""

# 基础类和枚举
from .base import (
    ToolPermission,
    ToolResult,
    ToolParameter,
    BaseTool,
)

# XML工具调用解析
from .xml_parser import (
    ToolCall,
    XMLToolCallParser,
    parse_tool_calls,
)

# XML格式化（工具说明 + 结果序列化）
from .xml_formatter import (
    generate_tool_instruction,
    format_result,
)

# 具体工具实现
from .implementations import *

__all__ = [
    # 基础类
    "ToolPermission",
    "ToolResult",
    "ToolParameter",
    "BaseTool",

    # XML解析
    "ToolCall",
    "XMLToolCallParser",
    "parse_tool_calls",

    # XML格式化
    "generate_tool_instruction",
    "format_result",

    # 具体工具（从implementations导入）
    "WebSearchTool",
    "WebFetchTool",
    "ArtifactMemory",
    "ArtifactVersionMemory",
    "ArtifactManager",
    "CreateArtifactTool",
    "UpdateArtifactTool",
    "RewriteArtifactTool",
    "ReadArtifactTool",
    "create_artifact_tools",
    "CallSubagentTool",
]

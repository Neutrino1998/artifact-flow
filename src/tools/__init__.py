"""
工具系统模块
提供工具基类、注册、XML解析和提示词生成功能
"""

# 基础类和枚举
from .base import (
    ToolPermission,
    ToolResult,
    ToolParameter,
    BaseTool,
)

# 工具注册和管理
from .registry import (
    AgentToolkit,
    ToolRegistry,
)

# XML工具调用解析
from .xml_parser import (
    ToolCall,
    XMLToolCallParser,
    parse_tool_calls,
)

# 提示词生成
from .prompt_generator import (
    ToolPromptGenerator,
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

    # 注册管理
    "AgentToolkit",
    "ToolRegistry",

    # XML解析
    "ToolCall",
    "XMLToolCallParser",
    "parse_tool_calls",

    # 提示词生成
    "ToolPromptGenerator",
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
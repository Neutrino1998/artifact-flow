"""
工具系统模块
提供工具基类、注册、权限控制和提示词生成功能
"""

# 基础类和枚举
from .base import (
    ToolPermission,
    ToolResult,
    ToolParameter,
    BaseTool,
    SyncBaseTool
)

# 工具注册和管理
from .registry import (
    AgentToolkit,
    ToolRegistry,
)

# 权限控制
from .permissions import (
    PermissionRequest,
    PermissionGrant,
    PermissionManager,
    check_permission,
    grant_permission,
    get_permission_manager
)

# 提示词生成
from .prompt_generator import (
    ToolPromptGenerator,
    generate_tool_prompt,
    format_result
)

# 具体工具实现
from .implementations import *

__all__ = [
    # 基础类
    "ToolPermission",
    "ToolResult",
    "ToolParameter",
    "BaseTool",
    "SyncBaseTool",

    # 注册管理
    "AgentToolkit",
    "ToolRegistry",

    # 权限控制
    "PermissionRequest",
    "PermissionGrant",
    "PermissionManager",
    "check_permission",
    "grant_permission",
    "get_permission_manager",

    # 提示词生成
    "ToolPromptGenerator",
    "generate_tool_prompt",
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
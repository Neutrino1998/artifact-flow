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
    get_registry,
    register_tool,
    create_agent_toolkit,
    get_agent_toolkit
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
    "get_registry",
    "register_tool",
    "create_agent_toolkit",
    "get_agent_toolkit",
    
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
    "Artifact",
    "ArtifactStore",
    "CreateArtifactTool",
    "UpdateArtifactTool",
    "RewriteArtifactTool", 
    "ReadArtifactTool",
    "register_web_search_tool",
    "register_web_fetch_tool",
    "register_artifact_tools",
    "get_artifact_store",
]
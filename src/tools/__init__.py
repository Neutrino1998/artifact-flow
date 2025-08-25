 
"""
工具系统模块
提供工具基类、注册、权限控制和提示词生成功能
"""

# 基础类和枚举
from .base import (
    BaseTool,
    SyncBaseTool,
    ToolResult,
    ToolParameter,
    ToolPermission
)

# 注册系统
from .registry import (
    ToolRegistry,
    register_tool,
    get_tool,
    list_tools,
    execute_tool,
    get_registry
)

# 提示词生成
from .prompt_generator import (
    ToolPromptGenerator,
    generate_tool_prompt,
    format_result
)

# 权限管理
from .permissions import (
    PermissionManager,
    PermissionRequest,
    PermissionGrant,
    check_permission,
    grant_permission,
    get_permission_manager
)

__all__ = [
    # Base
    "BaseTool",
    "SyncBaseTool", 
    "ToolResult",
    "ToolParameter",
    "ToolPermission",
    
    # Registry
    "ToolRegistry",
    "register_tool",
    "get_tool",
    "list_tools",
    "execute_tool",
    "get_registry",
    
    # Prompt Generator
    "ToolPromptGenerator",
    "generate_tool_prompt",
    "format_result",
    
    # Permissions
    "PermissionManager",
    "PermissionRequest",
    "PermissionGrant",
    "check_permission",
    "grant_permission",
    "get_permission_manager",
]
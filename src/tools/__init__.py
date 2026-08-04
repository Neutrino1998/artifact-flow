"""
工具系统模块
提供工具基类、native tool schema 和具体工具实现
"""

# 基础类和枚举
from .base import (
    ToolPermission,
    ToolResult,
    BaseTool,
)

# Artifact envelope 公共渲染器
from .artifact_envelope import (
    ArtifactSlice,
    render_artifact_slice,
    make_preview_slice,
)

# 具体工具实现
from .builtin import *

__all__ = [
    # 基础类
    "ToolPermission",
    "ToolResult",
    "BaseTool",

    # Artifact envelope
    "ArtifactSlice",
    "render_artifact_slice",
    "make_preview_slice",

    # 内置工具（从builtin导入）
    "WebSearchTool",
    "WebFetchTool",
    "ArtifactMemory",
    "ArtifactVersionMemory",
    "ArtifactWorkingSet",
    "ArtifactService",
    "CreateArtifactTool",
    "UpdateArtifactTool",
    "RewriteArtifactTool",
    "ReadArtifactTool",
    "create_artifact_tools",
    "CallSubagentTool",
]

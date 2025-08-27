"""
具体工具实现
"""

# Web搜索工具
from .web_search import (
    WebSearchTool,
    register_web_search_tool
)

# Web抓取工具
from .web_fetch import (
    WebFetchTool,
    SimpleFetchTool,
    register_web_fetch_tool
)

# Artifact操作工具
from .artifact_ops import (
    Artifact,
    ArtifactVersion,
    ArtifactStore,
    CreateArtifactTool,
    UpdateArtifactTool, 
    RewriteArtifactTool,
    ReadArtifactTool,
    register_artifact_tools,
    get_artifact_store,
    TASK_PLAN_TEMPLATE,
    RESULT_TEMPLATE
)

# Subagent调用工具（路由机制）
from .call_subagent import (
    CallSubagentTool,
    register_call_subagent_tool
)

__all__ = [
    # Web工具
    "WebSearchTool",
    "WebFetchTool",
    "SimpleFetchTool",
    
    # Artifact工具
    "Artifact", 
    "ArtifactVersion",
    "ArtifactStore",
    "CreateArtifactTool",
    "UpdateArtifactTool",
    "RewriteArtifactTool", 
    "ReadArtifactTool",
    
    # Subagent调用工具
    "CallSubagentTool",
    
    # 注册函数
    "register_web_search_tool",
    "register_web_fetch_tool",
    "register_artifact_tools",
    "register_call_subagent_tool",
    
    # 便捷函数和常量
    "get_artifact_store",
    "TASK_PLAN_TEMPLATE",
    "RESULT_TEMPLATE"
]
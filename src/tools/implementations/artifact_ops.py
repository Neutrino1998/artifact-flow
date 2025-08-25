"""
Artifact操作工具
提供创建、更新、读取Artifact的功能
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from ..base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger

logger = get_logger("ArtifactOps")


class ArtifactStore:
    """Artifact存储（简单的内存存储）"""
    
    def __init__(self):
        self.artifacts: Dict[str, Dict[str, Any]] = {}
    
    def create(self, artifact_id: str, content: Any, metadata: Dict = None) -> bool:
        """创建Artifact"""
        if artifact_id in self.artifacts:
            return False
        
        self.artifacts[artifact_id] = {
            "id": artifact_id,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "version": 1
        }
        return True
    
    def update(self, artifact_id: str, content: Any, metadata: Dict = None) -> bool:
        """更新Artifact"""
        if artifact_id not in self.artifacts:
            return False
        
        artifact = self.artifacts[artifact_id]
        artifact["content"] = content
        artifact["updated_at"] = datetime.now()
        artifact["version"] += 1
        
        if metadata:
            artifact["metadata"].update(metadata)
        
        return True
    
    def get(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """获取Artifact"""
        return self.artifacts.get(artifact_id)
    
    def list_artifacts(self) -> List[str]:
        """列出所有Artifact ID"""
        return list(self.artifacts.keys())


# 全局Artifact存储
_artifact_store = ArtifactStore()


class CreateArtifactTool(BaseTool):
    """
    创建Artifact工具
    用于创建新的Task Plan或Result Artifact
    """
    
    def __init__(self):
        super().__init__(
            name="create_artifact",
            description="Create a new artifact for task planning or results",
            permission=ToolPermission.NOTIFY  # 创建后通知
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="artifact_id",
                type="string",
                description="Unique identifier for the artifact (e.g., 'task_plan', 'results')",
                required=True
            ),
            ToolParameter(
                name="artifact_type",
                type="string",
                description="Type of artifact: 'task_plan' or 'result'",
                required=True
            ),
            ToolParameter(
                name="title",
                type="string",
                description="Title of the artifact",
                required=True
            ),
            ToolParameter(
                name="content",
                type="object",
                description="Initial content of the artifact",
                required=True
            ),
            ToolParameter(
                name="metadata",
                type="object",
                description="Additional metadata",
                required=False,
                default={}
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact_id = params.get("artifact_id")
        artifact_type = params.get("artifact_type")
        title = params.get("title")
        content = params.get("content")
        metadata = params.get("metadata", {})
        
        # 添加类型和标题到元数据
        metadata.update({
            "type": artifact_type,
            "title": title
        })
        
        # 创建Artifact
        success = _artifact_store.create(artifact_id, content, metadata)
        
        if success:
            logger.info(f"Created artifact: {artifact_id} (type: {artifact_type})")
            return ToolResult(
                success=True,
                data={
                    "artifact_id": artifact_id,
                    "message": f"Artifact '{artifact_id}' created successfully"
                }
            )
        else:
            return ToolResult(
                success=False,
                error=f"Artifact '{artifact_id}' already exists"
            )


class UpdateArtifactTool(BaseTool):
    """
    更新Artifact工具
    用于更新现有Artifact的内容
    """
    
    def __init__(self):
        super().__init__(
            name="update_artifact",
            description="Update an existing artifact's content",
            permission=ToolPermission.PUBLIC  # 更新是公开权限
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="artifact_id",
                type="string",
                description="ID of the artifact to update",
                required=True
            ),
            ToolParameter(
                name="content",
                type="object",
                description="New content for the artifact",
                required=True
            ),
            ToolParameter(
                name="merge",
                type="boolean",
                description="Whether to merge with existing content or replace",
                required=False,
                default=False
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact_id = params.get("artifact_id")
        new_content = params.get("content")
        merge = params.get("merge", False)
        
        # 如果是合并模式，先获取现有内容
        if merge:
            existing = _artifact_store.get(artifact_id)
            if existing and isinstance(existing["content"], dict) and isinstance(new_content, dict):
                # 深度合并字典
                merged_content = {**existing["content"], **new_content}
                new_content = merged_content
        
        # 更新Artifact
        success = _artifact_store.update(artifact_id, new_content)
        
        if success:
            artifact = _artifact_store.get(artifact_id)
            logger.info(f"Updated artifact: {artifact_id} (version: {artifact['version']})")
            return ToolResult(
                success=True,
                data={
                    "artifact_id": artifact_id,
                    "version": artifact["version"],
                    "message": f"Artifact '{artifact_id}' updated successfully"
                }
            )
        else:
            return ToolResult(
                success=False,
                error=f"Artifact '{artifact_id}' not found"
            )


class ReadArtifactTool(BaseTool):
    """
    读取Artifact工具
    用于读取Artifact的内容
    """
    
    def __init__(self):
        super().__init__(
            name="read_artifact",
            description="Read the content of an artifact",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="artifact_id",
                type="string",
                description="ID of the artifact to read",
                required=True
            ),
            ToolParameter(
                name="include_metadata",
                type="boolean",
                description="Whether to include metadata in the response",
                required=False,
                default=False
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact_id = params.get("artifact_id")
        include_metadata = params.get("include_metadata", False)
        
        artifact = _artifact_store.get(artifact_id)
        
        if artifact:
            result_data = {
                "artifact_id": artifact_id,
                "content": artifact["content"]
            }
            
            if include_metadata:
                result_data["metadata"] = artifact["metadata"]
                result_data["version"] = artifact["version"]
                result_data["updated_at"] = artifact["updated_at"].isoformat()
            
            return ToolResult(
                success=True,
                data=result_data
            )
        else:
            return ToolResult(
                success=False,
                error=f"Artifact '{artifact_id}' not found"
            )


class ListArtifactsTool(BaseTool):
    """
    列出Artifacts工具
    用于获取所有可用的Artifact列表
    """
    
    def __init__(self):
        super().__init__(
            name="list_artifacts",
            description="List all available artifacts",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="artifact_type",
                type="string",
                description="Filter by artifact type (optional)",
                required=False,
                default=None
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact_type = params.get("artifact_type")
        
        artifacts = []
        for artifact_id in _artifact_store.list_artifacts():
            artifact = _artifact_store.get(artifact_id)
            
            # 类型过滤
            if artifact_type and artifact["metadata"].get("type") != artifact_type:
                continue
            
            artifacts.append({
                "id": artifact_id,
                "type": artifact["metadata"].get("type", "unknown"),
                "title": artifact["metadata"].get("title", "Untitled"),
                "version": artifact["version"],
                "updated_at": artifact["updated_at"].isoformat()
            })
        
        return ToolResult(
            success=True,
            data={
                "artifacts": artifacts,
                "count": len(artifacts)
            }
        )


# 便捷函数
def register_artifact_tools():
    """注册所有Artifact操作工具"""
    from ..registry import register_tool
    
    register_tool(CreateArtifactTool())
    register_tool(UpdateArtifactTool())
    register_tool(ReadArtifactTool())
    register_tool(ListArtifactsTool())
    
    logger.info("Registered artifact tools: create_artifact, update_artifact, read_artifact, list_artifacts")


def get_artifact_store() -> ArtifactStore:
    """获取Artifact存储实例"""
    return _artifact_store
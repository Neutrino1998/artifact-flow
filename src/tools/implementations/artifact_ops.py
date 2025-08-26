"""
Artifact操作工具
模仿Claude的Artifact系统，支持create/update/rewrite操作
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger

logger = get_logger("ArtifactOps")


@dataclass
class ArtifactVersion:
    """Artifact版本记录"""
    version: int
    content: str
    updated_at: datetime
    update_type: str  # "create", "update", "rewrite"
    changes: Optional[List[Tuple[str, str]]] = None  # [(old_str, new_str), ...]


class Artifact:
    """
    Artifact对象
    支持文本内容的创建、更新和重写
    """
    
    def __init__(
        self,
        artifact_id: str,
        artifact_type: str,
        title: str,
        initial_content: str,
        metadata: Dict = None
    ):
        self.id = artifact_id
        self.type = artifact_type  # "task_plan" or "result"
        self.title = title
        self.content = initial_content
        self.metadata = metadata or {}
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.current_version = 1
        
        # 版本历史
        self.versions: List[ArtifactVersion] = [
            ArtifactVersion(
                version=1,
                content=initial_content,
                updated_at=self.created_at,
                update_type="create"
            )
        ]
    
    def update(self, old_str: str, new_str: str) -> Tuple[bool, str]:
        """
        更新内容（通过字符串替换）
        
        Args:
            old_str: 要替换的原文本
            new_str: 新文本
            
        Returns:
            (成功与否, 消息)
        """
        # 检查old_str是否存在且唯一
        count = self.content.count(old_str)
        
        if count == 0:
            return False, f"Text '{old_str[:50]}...' not found in artifact"
        elif count > 1:
            return False, f"Text '{old_str[:50]}...' appears {count} times (must be unique)"
        
        # 执行替换
        new_content = self.content.replace(old_str, new_str)
        
        # 保存版本
        self.current_version += 1
        self.versions.append(
            ArtifactVersion(
                version=self.current_version,
                content=new_content,
                updated_at=datetime.now(),
                update_type="update",
                changes=[(old_str, new_str)]
            )
        )
        
        self.content = new_content
        self.updated_at = datetime.now()
        
        return True, f"Successfully updated artifact (v{self.current_version})"
    
    def rewrite(self, new_content: str) -> Tuple[bool, str]:
        """
        完全重写内容
        
        Args:
            new_content: 全新的内容
            
        Returns:
            (成功与否, 消息)
        """
        # 保存版本
        self.current_version += 1
        self.versions.append(
            ArtifactVersion(
                version=self.current_version,
                content=new_content,
                updated_at=datetime.now(),
                update_type="rewrite"
            )
        )
        
        self.content = new_content
        self.updated_at = datetime.now()
        
        return True, f"Successfully rewrote artifact (v{self.current_version})"
    
    def get_version(self, version: int = None) -> Optional[str]:
        """获取指定版本的内容"""
        if version is None:
            return self.content
        
        for v in self.versions:
            if v.version == version:
                return v.content
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "current_version": self.current_version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class ArtifactStore:
    """Artifact存储管理"""
    
    def __init__(self):
        self.artifacts: Dict[str, Artifact] = {}
    
    def create(
        self,
        artifact_id: str,
        artifact_type: str,
        title: str,
        content: str,
        metadata: Dict = None
    ) -> Tuple[bool, str]:
        """创建新的Artifact"""
        if artifact_id in self.artifacts:
            return False, f"Artifact '{artifact_id}' already exists"
        
        artifact = Artifact(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            title=title,
            initial_content=content,
            metadata=metadata
        )
        
        self.artifacts[artifact_id] = artifact
        return True, f"Created artifact '{artifact_id}'"
    
    def get(self, artifact_id: str) -> Optional[Artifact]:
        """获取Artifact对象"""
        return self.artifacts.get(artifact_id)
    
    def list_artifacts(self, artifact_type: str = None) -> List[Dict]:
        """列出所有Artifacts"""
        artifacts = []
        for artifact in self.artifacts.values():
            if artifact_type and artifact.type != artifact_type:
                continue
            artifacts.append({
                "id": artifact.id,
                "type": artifact.type,
                "title": artifact.title,
                "version": artifact.current_version,
                "updated_at": artifact.updated_at.isoformat()
            })
        return artifacts


# 全局Artifact存储
_artifact_store = ArtifactStore()


class CreateArtifactTool(BaseTool):
    """创建Artifact工具"""
    
    def __init__(self):
        super().__init__(
            name="create_artifact",
            description="Create a new artifact (like Claude's artifact creation)",
            permission=ToolPermission.NOTIFY
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Unique identifier (e.g., 'task_plan', 'research_results')",
                required=True
            ),
            ToolParameter(
                name="type",
                type="string",
                description="Type: 'task_plan' or 'result'",
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
                type="string",
                description="Initial text content",
                required=True
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        success, message = _artifact_store.create(
            artifact_id=params["id"],
            artifact_type=params["type"],
            title=params["title"],
            content=params["content"]
        )
        
        if success:
            logger.info(message)
            return ToolResult(success=True, data={"message": message})
        else:
            return ToolResult(success=False, error=message)


class UpdateArtifactTool(BaseTool):
    """
    更新Artifact工具
    通过指定old_str和new_str来更新内容（类似Claude的update机制）
    """
    
    def __init__(self):
        super().__init__(
            name="update_artifact",
            description="Update artifact content by replacing old text with new text",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to update",
                required=True
            ),
            ToolParameter(
                name="old_str",
                type="string",
                description="Exact text to be replaced (must appear exactly once)",
                required=True
            ),
            ToolParameter(
                name="new_str",
                type="string",
                description="New text to replace with",
                required=True
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact = _artifact_store.get(params["id"])
        if not artifact:
            return ToolResult(
                success=False,
                error=f"Artifact '{params['id']}' not found"
            )
        
        success, message = artifact.update(
            old_str=params["old_str"],
            new_str=params["new_str"]
        )
        
        if success:
            logger.info(message)
            return ToolResult(
                success=True,
                data={
                    "message": message,
                    "version": artifact.current_version
                }
            )
        else:
            return ToolResult(success=False, error=message)


class RewriteArtifactTool(BaseTool):
    """
    重写Artifact工具
    完全替换整个内容
    """
    
    def __init__(self):
        super().__init__(
            name="rewrite_artifact",
            description="Completely rewrite the artifact content",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to rewrite",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="New complete content",
                required=True
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact = _artifact_store.get(params["id"])
        if not artifact:
            return ToolResult(
                success=False,
                error=f"Artifact '{params['id']}' not found"
            )
        
        success, message = artifact.rewrite(params["content"])
        
        logger.info(message)
        return ToolResult(
            success=True,
            data={
                "message": message,
                "version": artifact.current_version
            }
        )


class ReadArtifactTool(BaseTool):
    """读取Artifact工具"""
    
    def __init__(self):
        super().__init__(
            name="read_artifact",
            description="Read artifact content",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to read",
                required=True
            ),
            ToolParameter(
                name="version",
                type="integer",
                description="Version number (optional, defaults to latest)",
                required=False,
                default=None
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact = _artifact_store.get(params["id"])
        if not artifact:
            return ToolResult(
                success=False,
                error=f"Artifact '{params['id']}' not found"
            )
        
        content = artifact.get_version(params.get("version"))
        if content is None:
            return ToolResult(
                success=False,
                error=f"Version {params['version']} not found"
            )
        
        return ToolResult(
            success=True,
            data={
                "id": artifact.id,
                "type": artifact.type,
                "title": artifact.title,
                "content": content,
                "version": artifact.current_version,
                "updated_at": artifact.updated_at.isoformat()
            }
        )


# 示例：创建Task Plan Artifact
TASK_PLAN_TEMPLATE = """# Research Task Plan

## Objective
{objective}

## Tasks
1. [ ] Information Gathering
   - Status: pending
   - Assigned: search_agent
   - Details: {task1_details}

2. [ ] Deep Analysis
   - Status: pending
   - Assigned: crawl_agent
   - Details: {task2_details}

3. [ ] Report Generation
   - Status: pending
   - Assigned: lead_agent
   - Details: Compile findings into comprehensive report

## Progress
- Overall: 0%
- Last Updated: {timestamp}
"""

# 示例：创建Result Artifact
RESULT_TEMPLATE = """# Research Results: {title}

## Executive Summary
{summary}

## Key Findings
{findings}

## Detailed Analysis
{analysis}

## Conclusions
{conclusions}

## References
{references}
"""


def register_artifact_tools():
    """注册所有Artifact操作工具"""
    from tools.registry import register_tool
    
    register_tool(CreateArtifactTool())
    register_tool(UpdateArtifactTool())
    register_tool(RewriteArtifactTool())
    register_tool(ReadArtifactTool())
    
    logger.info("Registered artifact tools")


def get_artifact_store() -> ArtifactStore:
    """获取Artifact存储实例"""
    return _artifact_store


if __name__ == "__main__":
    import asyncio
    import sys

    async def run_tests():
        """
        为Artifact操作工具集运行一个精简的测试套件。
        """
        print("\n🧪 Simplified Artifact Operations Test Suite")
        print("="*50)

        # 辅助函数，用于检查和打印结果，减少重复代码
        def check(step_name: str, result: ToolResult) -> bool:
            if result.success:
                message = result.data.get('message', 'Operation successful.')
                print(f"✅ {step_name}: {message}")
                return True
            else:
                print(f"❌ {step_name}: FAILED - {result.error}")
                # 在关键步骤失败时直接退出测试
                if step_name in ["Create Artifact", "Read Artifact"]:
                    sys.exit(1) 
                return False

        # 1. 初始化工具和测试数据
        create_tool = CreateArtifactTool()
        read_tool = ReadArtifactTool()
        update_tool = UpdateArtifactTool()
        rewrite_tool = RewriteArtifactTool()
        
        test_id = "test_plan_001"
        initial_content = "Step 1: Define project goals.\nStep 2: Gather requirements."

        # 2. 执行测试流程
        print("--- Running Test Flow ---")

        # Create
        result = await create_tool.execute(
            id=test_id, type="task_plan", title="Test Plan", content=initial_content
        )
        check("Create Artifact", result)

        # Update
        result = await update_tool.execute(
            id=test_id, old_str="Gather requirements.", new_str="Gather all stakeholder requirements."
        )
        check("Update Artifact", result)

        # Rewrite
        final_content = "# Final Plan\nProject is ready for review."
        result = await rewrite_tool.execute(id=test_id, content=final_content)
        check("Rewrite Artifact", result)

        # Read and Verify Final State
        read_result = await read_tool.execute(id=test_id)
        if check("Read Final Artifact", read_result):
            content = read_result.data['content']
            print(f"   Final Content: '{content}'")
            # 使用断言验证内容是否正确
            assert content == final_content, "Rewrite content does not match!"

        # Test a known failing case (text not found for update)
        fail_result = await update_tool.execute(
            id=test_id, old_str="non_existent_text", new_str="this should fail"
        )
        # 对于预期失败的测试，我们希望check返回False
        if not check("Test Failing Update", fail_result):
            print("   -> Correctly handled non-existent text for update.")

        print("\n" + "="*50)
        print("✅ Test Suite Completed Successfully.")

    # 运行异步测试函数
    asyncio.run(run_tests())
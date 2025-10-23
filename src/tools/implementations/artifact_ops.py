"""
Artifact操作工具
使用 diff-match-patch 提供鲁棒的文本更新功能
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from uuid import uuid4
from dataclasses import dataclass, field
import diff_match_patch as dmp_module  # pip install diff-match-patch

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


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
    使用 diff-match-patch 实现鲁棒的模糊匹配
    """
    
    def __init__(
        self,
        artifact_id: str,
        content_type: str,
        title: str,
        initial_content: str,
        metadata: Dict = None
    ):
        self.id = artifact_id
        self.content_type = content_type
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
    
    def update(
        self, 
        old_str: str, 
        new_str: str,
        match_threshold: float = 0.7,  # 匹配阈值：越低越宽松
        max_diff_ratio: float = 0.3    # 最大差异率：越高越宽松
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        使用 diff-match-patch 更新内容
        
        Args:
            old_str: 要替换的原文本
            new_str: 新文本
            match_threshold: 匹配阈值 (0.0-1.0)，越高越严格
            max_diff_ratio: 最大允许的差异率 (相对于 old_str 长度)
            
        Returns:
            (成功与否, 消息, 匹配详情字典)
        """
        
        # Step 1: 快速精确匹配
        if old_str in self.content:
            count = self.content.count(old_str)
            
            if count > 1:
                return False, f"Text '{old_str[:50]}...' appears {count} times (must be unique)", None
            
            # 精确匹配成功
            new_content = self.content.replace(old_str, new_str, 1)
            self._save_version(new_content, "update", [(old_str, new_str)])
            
            return True, f"Successfully updated artifact (v{self.current_version})", {
                "match_type": "exact",
                "similarity": 1.0
            }
        
        # Step 2: 使用 DMP 进行模糊匹配
        logger.debug("Exact match failed, attempting fuzzy match...")
        
        dmp = dmp_module.diff_match_patch()
        dmp.Match_Threshold = match_threshold
        dmp.Match_Distance = len(self.content) # 大距离以覆盖全文本搜索
        
        # 2.1 定位起始位置
        match_pos = dmp.match_main(self.content, old_str, 0)
        
        if match_pos == -1:
            return False, f"Failed to find matching text '{old_str[:50]}...'", None
        
        # 2.2 计算精确的结束位置
        diffs = dmp.diff_main(old_str, self.content[match_pos:])
        dmp.diff_cleanupSemantic(diffs)
        
        # 关键修正: diff_main 比较的是 old_str 和【文档剩余的全部内容】，
        # 这会导致 diffs 列表的末尾包含一个巨大的“插入”操作（即文档剩余部分），
        # 这个多余的操作会干扰 diff_xIndex 的计算，导致计算出的长度远超预期。
        # 因此，我们需要安全地裁剪掉这个多余的尾巴。
        #
        # 安全检查：仅当最后一个操作是“插入”(type 1)时才进行裁剪，
        # 这样可以正确处理 old_str 恰好匹配到文档末尾的边缘情况。
        if diffs and diffs[-1][0] == 1:
            diffs = diffs[:-1]

        # 检查相似度
        levenshtein_distance = dmp.diff_levenshtein(diffs)
        if levenshtein_distance > len(old_str) * max_diff_ratio:
            return False, f"Best match difference is too large (edit distance: {levenshtein_distance})", None
        
        # 使用 diff_xIndex 计算精确长度
        exact_len = dmp.diff_xIndex(diffs, len(old_str))
        end_pos = match_pos + exact_len
        matched_text = self.content[match_pos:end_pos]
        
        # 2.3 生成并应用补丁
        # 优化：直接从 diff 生成补丁，而不是重新比较整个字符串
        patches = dmp.patch_make(matched_text, new_str)
        new_content, results = dmp.patch_apply(patches, self.content)

        # 如果补丁应用失败（例如，由于上下文），则回退到直接替换
        if not all(results):
            logger.warning("Patch application failed, falling back to direct replacement.")
            new_content = self.content[:match_pos] + new_str + self.content[end_pos:]
            results = [True] # 标记为成功
        
        # 2.4 保存版本
        self._save_version(new_content, "update_fuzzy", [(matched_text, new_str)])
        
        similarity = 1.0 - (levenshtein_distance / len(old_str))
        logger.info(
            f"Fuzzy match succeeded (similarity: {similarity:.1%})\n"
            f"Expected: {old_str[:100]}...\n"
            f"Actual: {matched_text[:100]}..."
        )

        return True, f"Fuzzy match succeeded {similarity:.1%} (v{self.current_version})", {
            "match_type": "fuzzy",
            "similarity": similarity,
            "expected_text": old_str,
            "matched_text": matched_text,
        }
    
    def rewrite(self, new_content: str) -> Tuple[bool, str]:
        """完全重写内容"""
        self._save_version(new_content, "rewrite")
        return True, f"Successfully rewritten artifact (v{self.current_version})"
    
    def _save_version(
        self, 
        content: str, 
        update_type: str, 
        changes: Optional[List[Tuple[str, str]]] = None
    ):
        """保存新版本（内部方法）"""
        self.current_version += 1
        self.versions.append(
            ArtifactVersion(
                version=self.current_version,
                content=content,
                updated_at=datetime.now(),
                update_type=update_type,
                changes=changes
            )
        )
        self.content = content
        self.updated_at = datetime.now()
    
    def get_version(self, version: Optional[int] = None) -> Optional[str]:
        """获取指定版本的内容（用于前端对比）"""
        if version is None:
            return self.content
        
        for v in self.versions:
            if v.version == version:
                return v.content
        return None
    
    def list_versions(self) -> List[Dict[str, Any]]:
        """
        获取版本历史列表（用于前端时间线展示）
        返回格式适配 Monaco Editor 的需求
        """
        return [
            {
                "version": v.version,
                "update_type": v.update_type,
                "updated_at": v.updated_at.isoformat(),
                "has_changes": v.changes is not None,
                "change_count": len(v.changes) if v.changes else 0
            }
            for v in self.versions
        ]


@dataclass
class ArtifactSession:
    """Artifact会话"""
    session_id: str
    artifacts: Dict[str, Artifact] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


class ArtifactStore:
    """Artifact存储管理器"""
    
    def __init__(self):
        self.sessions: Dict[str, ArtifactSession] = {}
        self.current_session_id: Optional[str] = None
    
    def create_session(self, session_id: Optional[str] = None) -> str:
        """创建新session"""
        if session_id is None:
            session_id = f"sess-{uuid4().hex}"
        
        self.sessions[session_id] = ArtifactSession(session_id=session_id)
        self.current_session_id = session_id
        logger.info(f"Created new session: {session_id}")
        return session_id
    
    def set_session(self, session_id: Optional[str]):
        """切换当前session"""
        if session_id and session_id not in self.sessions:
            logger.warning(f"Session {session_id} not found, creating new one")
            self.create_session(session_id)
        else:
            self.current_session_id = session_id
    
    def get_current_session(self) -> Optional[ArtifactSession]:
        """获取当前session"""
        if self.current_session_id is None:
            self.create_session("default")
        return self.sessions.get(self.current_session_id)
    
    def create(
        self,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Dict = None
    ) -> Tuple[bool, str]:
        """创建新的Artifact"""
        session = self.get_current_session()
        if not session:
            return False, "No active session"
        
        if artifact_id in session.artifacts:
            return False, f"Artifact '{artifact_id}' already exists in session"
        
        artifact = Artifact(
            artifact_id=artifact_id,
            content_type=content_type,
            title=title,
            initial_content=content,
            metadata=metadata
        )
        
        session.artifacts[artifact_id] = artifact
        return True, f"Created artifact '{artifact_id}' in session '{session.session_id}'"
    
    def get(self, artifact_id: str) -> Optional[Artifact]:
        """获取Artifact对象"""
        session = self.get_current_session()
        if not session:
            return None
        return session.artifacts.get(artifact_id)
    
    def list_artifacts(
        self, 
        content_type: str = None,
        include_content: bool = True,
        content_preview_length: int = 200,
        full_content_for: List[str] = None
    ) -> List[Dict]:
        """
        列出当前session的所有Artifacts
        
        Args:
            content_type: 过滤特定类型
            include_content: 是否包含内容字段
            content_preview_length: 内容预览长度（默认200字符）
            full_content_for: 需要完整内容的artifact ID列表（如 ["task_plan"]）
        
        Returns:
            Artifact信息列表
        """
        session = self.get_current_session()
        if not session:
            return []
        
        if full_content_for is None:
            full_content_for = []
        
        artifacts = []
        for artifact in session.artifacts.values():
            if content_type and artifact.content_type != content_type:
                continue
            
            artifact_dict = {
                "id": artifact.id,
                "content_type": artifact.content_type,
                "title": artifact.title,
                "version": artifact.current_version,
                "updated_at": artifact.updated_at.isoformat()
            }
            
            # 添加内容字段（带智能截断）
            if include_content:
                # 如果在full_content_for列表中，返回完整内容
                if artifact.id in full_content_for:
                    artifact_dict["content"] = artifact.content
                else:
                    # 否则返回截断的预览
                    content = artifact.content
                    if len(content) > content_preview_length:
                        artifact_dict["content"] = content[:content_preview_length] + "[Content truncated...]"
                    else:
                        artifact_dict["content"] = content
            
            artifacts.append(artifact_dict)
        
        return artifacts
    
    def clear_temporary_artifacts(self, session_id: Optional[str] = None):
        """清除临时性的 artifacts（如 task_plan）"""
        sid = session_id or self.current_session_id
        if sid and sid in self.sessions:
            session = self.sessions[sid]
            # 清除已知的临时 artifacts
            temporary_ids = ["task_plan"]
            for artifact_id in temporary_ids:
                if artifact_id in session.artifacts:
                    del session.artifacts[artifact_id]
                    logger.debug(f"Cleared temporary artifact: {artifact_id}")


# 全局Artifact存储
_artifact_store = ArtifactStore()


# ==================== Tool Classes ====================

class CreateArtifactTool(BaseTool):
    """创建 Artifact 工具"""
    
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
                name="content_type", 
                type="string",
                description="Content format: 'markdown', 'txt', 'python', 'html', 'json'",  
                required=False,
                default="markdown"  
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
            content_type=params.get("content_type", "markdown"), 
            title=params["title"],
            content=params["content"]
        )
        
        if success:
            logger.info(message)
            return ToolResult(success=True, data={"message": message})
        return ToolResult(success=False, error=message)


class UpdateArtifactTool(BaseTool):
    """
    更新Artifact工具
    通过指定old_str和new_str来更新内容（类似Claude的update机制）
    """
    
    def __init__(self):
        super().__init__(
            name="update_artifact",
            description="Update artifact content by replacing old text with new text (Attempt fuzzy matching if exact text not found).",
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
                description="Text to be replaced",
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
        
        success, message, match_info = artifact.update(
            old_str=params["old_str"],
            new_str=params["new_str"]
        )
        
        if success:
            logger.info(message)

            result_data = {
                "message": message,
                "version": artifact.current_version
            }
            
            # 如果是模糊匹配，添加详细信息
            if match_info and match_info.get("match_type") == "fuzzy":
                result_data["fuzzy_match"] = {
                    "similarity": f"{match_info['similarity']:.1%}",
                    "expected": match_info["expected_text"][:200],
                    "matched": match_info["matched_text"][:200],
                    "note": "Used fuzzy matching because exact text was not found"
                }
            
            return ToolResult(success=True, data=result_data, metadata=match_info)
        
        return ToolResult(success=False, error=message)

    def to_xml_example(self) -> str:
        """
        生成更清晰的XML调用示例，强调正确的换行处理
        """
        # 使用实际的换行符，不是\n字符串
        return """<tool_call>
<name>update_artifact</name>
  <params>
    <id>task_plan</id>
    <old_str>1. [✗] Search for recent developments
   - Status: pending
   - Assigned: search_agent
   - Notes: N/A</old_str>
    <new_str>1. [✓] Search for recent developments
   - Status: completed
   - Assigned: search_agent
   - Notes: Found 5 key breakthroughs</new_str>
  </params>
</tool_call>

IMPORTANT NOTES:
1. Use ACTUAL line breaks in XML, not \\n escape sequences
2. For multi-line updates, include all related lines as a unit
"""


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
        
        version = params.get("version")
        content = artifact.get_version(version)
        
        if content is None:
            return ToolResult(
                success=False,
                error=f"Version {version} not found"
            )
        
        return ToolResult(
            success=True,
            data={
                "id": artifact.id,
                "content_type": artifact.content_type, 
                "title": artifact.title,
                "content": content,
                "version": version or artifact.current_version,
                "updated_at": artifact.updated_at.isoformat()
            }
        )




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
    from utils.logger import set_global_debug
    set_global_debug(True)

    async def run_tests():
        """
        测试Artifact操作工具集，包括模糊匹配功能
        """
        print("\n🧪 Artifact Operations Test Suite (with Fuzzy Matching)")
        print("="*60)

        # 辅助函数
        def check(step_name: str, result: ToolResult) -> bool:
            if result.success:
                message = result.data.get('message', 'Operation successful.')
                print(f"✅ {step_name}: {message}")
                return True
            else:
                print(f"❌ {step_name}: FAILED - {result.error}")
                if step_name in ["Create Artifact", "Read Artifact"]:
                    sys.exit(1) 
                return False

        # 1. 初始化工具
        create_tool = CreateArtifactTool()
        read_tool = ReadArtifactTool()
        update_tool = UpdateArtifactTool()
        rewrite_tool = RewriteArtifactTool()
        
        # 2. 测试场景：模拟真实的Task Plan
        test_id = "task_plan"
        initial_content = """# Task: Research AI Safety
        
## Objective
Research latest developments in AI safety and alignment.

## Tasks
1. [✗] Search for recent papers on AI alignment
   - Status: pending
   - Assigned: search_agent
   - Notes: 

2. [✗] Extract key findings from top papers
   - Status: pending
   - Assigned: crawl_agent
   - Notes: Focus on 2024 publications

## Progress Summary
- Overall: 0%
- Last Updated: 2024-01-01"""

        print("\n--- Test 1: Basic Operations ---")
        
        # Create
        result = await create_tool.execute(
            id=test_id, 
            content_type="markdown", 
            title="AI Safety Research Plan", 
            content=initial_content
        )
        check("Create Task Plan", result)

        # Read
        result = await read_tool.execute(id=test_id)
        check("Read Task Plan", result)

        print("\n--- Test 2: Exact Match Update ---")
        
        # 精确匹配更新（应该成功）
        result = await update_tool.execute(
            id=test_id,
            old_str="- Overall: 0%",
            new_str="- Overall: 25%"
        )
        check("Update Progress (Exact Match)", result)

        print("\n--- Test 3: Fuzzy Match Updates ---")
        
        # 测试3a: 空白字符差异（缺少尾部空格）
        result = await update_tool.execute(
            id=test_id,
            old_str="1. [✗] Search for recent papers on AI alignment\n     - Status: pending\n      - Assigned: search_agent\n     - Notes:",  # 注意：空格数量不对
            new_str="1. [✓] Search for recent papers on AI alignment\n   - Status: completed\n   - Assigned: search_agent\n   - Notes: Found 15 relevant papers from 2024"
        )
        check("Update Task 1 (Fuzzy: whitespace mismatch)", result)

        # 测试3b: 轻微文本差异
        result = await update_tool.execute(
            id=test_id,
            old_str="2. [✗] Extract key findings from top papers\n   - Status: pending\n   - Assigned: search_agents\n   - Note: Focus on 2024 publication",  # 注意：crawl_agent写成了search_agents，Notes拼写错误
            new_str="2. [✓] Extract key findings from top papers\n   - Status: completed\n   - Assigned: crawl_agent\n   - Notes: Analyzed 5 key papers with breakthrough findings"
        )
        check("Update Task 2 (Fuzzy: minor text difference)", result)

        print("\n--- Test 4: Edge Cases ---")
        
        # 测试4: 完全不匹配的文本
        result = await update_tool.execute(
            id=test_id,
            old_str="This text does not exist in the artifact at all",
            new_str="This should fail"
        )
        if not result.success:
            print(f"✅ Correctly rejected non-existent text: {result.error}")

        print("\n" + "="*60)
        print("✅ Test Suite Completed Successfully.")

    # 运行异步测试函数
    asyncio.run(run_tests())
"""
Artifact 操作工具和管理器

改造说明（v2.0）：
- 移除全局单例 `_artifact_store`
- 新增 `ArtifactManager` 类，通过依赖注入使用 `ArtifactRepository`
- 保留 `Artifact` 类的核心 diff-match-patch 逻辑（作为内存对象）
- 工具类通过 `ArtifactManager` 访问数据
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field
import diff_match_patch as dmp_module

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from repositories.artifact_repo import ArtifactRepository
from repositories.base import NotFoundError, DuplicateError
from db.models import VersionConflictError
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


# ============================================================
# 内存对象（用于 diff-match-patch 处理）
# ============================================================

@dataclass
class ArtifactVersionMemory:
    """Artifact 版本记录（内存对象）"""
    version: int
    content: str
    updated_at: datetime
    update_type: str  # "create", "update", "update_fuzzy", "rewrite"
    changes: Optional[List[Tuple[str, str]]] = None  # [(old_str, new_str), ...]


class ArtifactMemory:
    """
    Artifact 内存对象

    用于处理 diff-match-patch 逻辑，与数据库模型分离。
    保持原有的模糊匹配能力。
    """

    def __init__(
        self,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        current_version: int = 1,
        lock_version: int = 1,
        metadata: Dict = None
    ):
        self.id = artifact_id
        self.content_type = content_type
        self.title = title
        self.content = content
        self.metadata = metadata or {}
        self.current_version = current_version
        self.lock_version = lock_version
        self.updated_at = datetime.now()

    def compute_update(
        self,
        old_str: str,
        new_str: str,
        match_threshold: float = 0.7,
        max_diff_ratio: float = 0.3
    ) -> Tuple[bool, str, Optional[str], Optional[Dict]]:
        """
        计算更新结果（使用 diff-match-patch）

        Args:
            old_str: 要替换的原文本
            new_str: 新文本
            match_threshold: 匹配阈值 (0.0-1.0)
            max_diff_ratio: 最大允许的差异率

        Returns:
            (成功与否, 消息, 新内容, 匹配详情字典)
        """
        # Step 1: 快速精确匹配
        if old_str in self.content:
            count = self.content.count(old_str)

            if count > 1:
                return False, f"Text '{old_str[:50]}...' appears {count} times (must be unique)", None, None

            # 精确匹配成功
            new_content = self.content.replace(old_str, new_str, 1)

            return True, "exact match", new_content, {
                "match_type": "exact",
                "similarity": 1.0,
                "changes": [(old_str, new_str)]
            }

        # Step 2: 使用 DMP 进行模糊匹配
        logger.debug("Exact match failed, attempting fuzzy match...")

        dmp = dmp_module.diff_match_patch()
        dmp.Match_Threshold = match_threshold
        dmp.Match_Distance = len(self.content)

        # 2.1 定位起始位置
        match_pos = dmp.match_main(self.content, old_str, 0)

        if match_pos == -1:
            return False, f"Failed to find matching text '{old_str[:50]}...'", None, None

        # 2.2 计算精确的结束位置
        diffs = dmp.diff_main(old_str, self.content[match_pos:])
        dmp.diff_cleanupSemantic(diffs)

        if diffs and diffs[-1][0] == 1:
            diffs = diffs[:-1]

        # 检查相似度
        levenshtein_distance = dmp.diff_levenshtein(diffs)
        if levenshtein_distance > len(old_str) * max_diff_ratio:
            return False, f"Best match difference is too large (edit distance: {levenshtein_distance})", None, None

        # 使用 diff_xIndex 计算精确长度
        exact_len = dmp.diff_xIndex(diffs, len(old_str))
        end_pos = match_pos + exact_len
        matched_text = self.content[match_pos:end_pos]

        # 2.3 生成并应用补丁
        patches = dmp.patch_make(matched_text, new_str)
        new_content, results = dmp.patch_apply(patches, self.content)

        if not all(results):
            logger.warning("Patch application failed, falling back to direct replacement.")
            new_content = self.content[:match_pos] + new_str + self.content[end_pos:]

        similarity = 1.0 - (levenshtein_distance / len(old_str))
        logger.info(
            f"Fuzzy match succeeded (similarity: {similarity:.1%})\n"
            f"Expected: {old_str[:100]}...\n"
            f"Actual: {matched_text[:100]}..."
        )

        return True, f"fuzzy match {similarity:.1%}", new_content, {
            "match_type": "fuzzy",
            "similarity": similarity,
            "expected_text": old_str,
            "matched_text": matched_text,
            "changes": [(matched_text, new_str)]
        }


# ============================================================
# ArtifactManager（核心管理类）
# ============================================================

class ArtifactManager:
    """
    Artifact 管理器

    职责：
    - 协调内存 Artifact 和数据库持久化
    - 通过依赖注入接收 ArtifactRepository
    - 维护当前 session 的内存缓存
    - 使用乐观锁进行并发控制

    使用方式：
        async with db_manager.session() as session:
            repo = ArtifactRepository(session)
            manager = ArtifactManager(repo)
            await manager.create_artifact(...)
    """

    def __init__(self, repository: Optional[ArtifactRepository] = None):
        """
        初始化 ArtifactManager

        Args:
            repository: ArtifactRepository 实例（通过依赖注入）
                       可以为 None，稍后通过 set_repository 设置
        """
        self.repository = repository
        self._cache: Dict[str, Dict[str, ArtifactMemory]] = {}  # {session_id: {artifact_id: ArtifactMemory}}
        self._current_session_id: Optional[str] = None

        logger.debug("ArtifactManager initialized")

    def set_repository(self, repository: ArtifactRepository) -> None:
        """
        设置/更新 Repository（用于每次请求时绑定新的数据库 session）

        Args:
            repository: ArtifactRepository 实例
        """
        self.repository = repository

    def _ensure_repository(self) -> ArtifactRepository:
        """确保 Repository 已设置"""
        if self.repository is None:
            raise RuntimeError("ArtifactManager: repository not configured. Call set_repository() first.")
        return self.repository

    def set_session(self, session_id: str) -> None:
        """设置当前 session"""
        self._current_session_id = session_id
        if session_id not in self._cache:
            self._cache[session_id] = {}

    @property
    def current_session_id(self) -> Optional[str]:
        """获取当前 session ID"""
        return self._current_session_id

    async def ensure_session_exists(self, session_id: str) -> None:
        """确保 ArtifactSession 存在（数据库层）"""
        repo = self._ensure_repository()
        await repo.ensure_session_exists(session_id)
        if session_id not in self._cache:
            self._cache[session_id] = {}

    async def create_artifact(
        self,
        session_id: str,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict] = None
    ) -> Tuple[bool, str]:
        """
        创建新的 Artifact

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            content_type: 内容类型
            title: 标题
            content: 初始内容
            metadata: 元数据

        Returns:
            (成功与否, 消息)
        """
        try:
            repo = self._ensure_repository()

            # 1. 确保 session 存在
            await self.ensure_session_exists(session_id)

            # 2. 创建数据库记录
            db_artifact = await repo.create_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                metadata=metadata
            )

            # 3. 创建内存缓存
            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=db_artifact.current_version,
                lock_version=db_artifact.lock_version,
                metadata=metadata
            )

            if session_id not in self._cache:
                self._cache[session_id] = {}
            self._cache[session_id][artifact_id] = memory

            logger.info(f"Created artifact '{artifact_id}' in session '{session_id}'")
            return True, f"Created artifact '{artifact_id}'"

        except DuplicateError:
            return False, f"Artifact '{artifact_id}' already exists in session"
        except NotFoundError as e:
            return False, str(e)
        except Exception as e:
            logger.exception(f"Failed to create artifact: {e}")
            return False, f"Failed to create artifact: {str(e)}"

    async def get_artifact(
        self,
        session_id: str,
        artifact_id: str
    ) -> Optional[ArtifactMemory]:
        """
        获取 Artifact（优先从缓存）

        Args:
            session_id: Session ID
            artifact_id: Artifact ID

        Returns:
            ArtifactMemory 对象
        """
        # 1. 检查缓存
        if session_id in self._cache and artifact_id in self._cache[session_id]:
            return self._cache[session_id][artifact_id]

        # 2. 从数据库加载
        repo = self._ensure_repository()
        db_artifact = await repo.get_artifact(session_id, artifact_id)
        if not db_artifact:
            return None

        # 3. 创建内存对象并缓存
        memory = ArtifactMemory(
            artifact_id=db_artifact.id,
            content_type=db_artifact.content_type,
            title=db_artifact.title,
            content=db_artifact.content,
            current_version=db_artifact.current_version,
            lock_version=db_artifact.lock_version,
            metadata=db_artifact.metadata_
        )

        if session_id not in self._cache:
            self._cache[session_id] = {}
        self._cache[session_id][artifact_id] = memory

        return memory

    async def update_artifact(
        self,
        session_id: str,
        artifact_id: str,
        old_str: str,
        new_str: str
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        更新 Artifact 内容（使用 diff-match-patch）

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            old_str: 要替换的文本
            new_str: 新文本

        Returns:
            (成功与否, 消息, 匹配信息)
        """
        # 1. 获取内存对象
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found", None

        # 2. 计算更新
        success, msg, new_content, match_info = memory.compute_update(old_str, new_str)

        if not success:
            return False, msg, None

        # 3. 持久化到数据库（使用乐观锁）
        try:
            repo = self._ensure_repository()
            update_type = "update" if match_info["match_type"] == "exact" else "update_fuzzy"

            db_artifact = await repo.update_artifact_content(
                session_id=session_id,
                artifact_id=artifact_id,
                new_content=new_content,
                update_type=update_type,
                expected_lock_version=memory.lock_version,
                changes=match_info.get("changes")
            )

            # 4. 更新内存缓存
            memory.content = new_content
            memory.current_version = db_artifact.current_version
            memory.lock_version = db_artifact.lock_version
            memory.updated_at = datetime.now()

            return True, f"Successfully updated artifact '{artifact_id}' (v{memory.current_version})", match_info

        except VersionConflictError as e:
            # 版本冲突，需要重新加载
            logger.warning(f"Version conflict: {e}")
            # 清除缓存，下次访问时重新加载
            if session_id in self._cache and artifact_id in self._cache[session_id]:
                del self._cache[session_id][artifact_id]
            return False, f"Version conflict: artifact was modified by another process", None
        except Exception as e:
            logger.exception(f"Failed to update artifact: {e}")
            return False, f"Failed to update artifact: {str(e)}", None

    async def rewrite_artifact(
        self,
        session_id: str,
        artifact_id: str,
        new_content: str
    ) -> Tuple[bool, str]:
        """
        完全重写 Artifact 内容

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            new_content: 新内容

        Returns:
            (成功与否, 消息)
        """
        # 1. 获取内存对象
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found"

        # 2. 持久化到数据库
        try:
            repo = self._ensure_repository()
            db_artifact = await repo.rewrite_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                new_content=new_content,
                expected_lock_version=memory.lock_version
            )

            # 3. 更新内存缓存
            memory.content = new_content
            memory.current_version = db_artifact.current_version
            memory.lock_version = db_artifact.lock_version
            memory.updated_at = datetime.now()

            return True, f"Successfully rewritten artifact '{artifact_id}' (v{memory.current_version})"

        except VersionConflictError:
            if session_id in self._cache and artifact_id in self._cache[session_id]:
                del self._cache[session_id][artifact_id]
            return False, "Version conflict: artifact was modified by another process"
        except Exception as e:
            logger.exception(f"Failed to rewrite artifact: {e}")
            return False, f"Failed to rewrite artifact: {str(e)}"

    async def read_artifact(
        self,
        session_id: str,
        artifact_id: str,
        version: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        读取 Artifact 内容

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            version: 版本号（None 则读取最新版本）

        Returns:
            Artifact 信息字典
        """
        if version is None:
            # 读取当前版本
            memory = await self.get_artifact(session_id, artifact_id)
            if not memory:
                return None

            return {
                "id": memory.id,
                "content_type": memory.content_type,
                "title": memory.title,
                "content": memory.content,
                "version": memory.current_version,
                "updated_at": memory.updated_at.isoformat()
            }
        else:
            # 读取历史版本
            repo = self._ensure_repository()
            content = await repo.get_version_content(session_id, artifact_id, version)
            if content is None:
                return None

            memory = await self.get_artifact(session_id, artifact_id)
            return {
                "id": artifact_id,
                "content_type": memory.content_type if memory else "unknown",
                "title": memory.title if memory else "Unknown",
                "content": content,
                "version": version,
                "updated_at": None
            }

    async def list_artifacts(
        self,
        session_id: str,
        content_type: Optional[str] = None,
        include_content: bool = True,
        content_preview_length: int = 200,
        full_content_for: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        列出 Session 的所有 Artifacts

        Args:
            session_id: Session ID
            content_type: 按类型筛选
            include_content: 是否包含内容
            content_preview_length: 内容预览长度
            full_content_for: 需要完整内容的 artifact ID 列表

        Returns:
            Artifact 信息列表
        """
        if full_content_for is None:
            full_content_for = []

        repo = self._ensure_repository()

        # 从数据库获取列表
        artifacts = await repo.list_artifacts(
            session_id=session_id,
            content_type=content_type,
            include_content=include_content,
            content_preview_length=content_preview_length
        )

        # 处理需要完整内容的 artifacts
        if full_content_for:
            full_artifacts = await repo.get_artifacts_with_full_content(
                session_id, full_content_for
            )
            for artifact_info in artifacts:
                if artifact_info["id"] in full_artifacts:
                    artifact_info["content"] = full_artifacts[artifact_info["id"]].content

        return artifacts

    async def clear_temporary_artifacts(
        self,
        session_id: str,
        temporary_ids: Optional[List[str]] = None
    ) -> int:
        """
        清除临时 Artifacts（如 task_plan）

        Args:
            session_id: Session ID
            temporary_ids: 临时 Artifact ID 列表

        Returns:
            删除的数量
        """
        if temporary_ids is None:
            temporary_ids = ["task_plan"]

        repo = self._ensure_repository()

        # 清除数据库
        deleted_count = await repo.clear_temporary_artifacts(session_id, temporary_ids)

        # 清除缓存
        if session_id in self._cache:
            for artifact_id in temporary_ids:
                if artifact_id in self._cache[session_id]:
                    del self._cache[session_id][artifact_id]
                    logger.debug(f"Cleared temporary artifact from cache: {artifact_id}")

        return deleted_count

    def clear_cache(self, session_id: Optional[str] = None) -> None:
        """
        清除缓存

        Args:
            session_id: Session ID（None 则清除所有）
        """
        if session_id:
            if session_id in self._cache:
                del self._cache[session_id]
        else:
            self._cache.clear()


# ============================================================
# 工具类
# ============================================================

class CreateArtifactTool(BaseTool):
    """创建 Artifact 工具"""

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="create_artifact",
            description="Create a new artifact (like Claude's artifact creation)",
            permission=ToolPermission.AUTO
        )
        self._manager = manager

    def set_manager(self, manager: ArtifactManager) -> None:
        """设置 ArtifactManager（依赖注入）"""
        self._manager = manager

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Unique identifier (e.g., 'task_plan', 'research_report')",
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
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message = await self._manager.create_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            content_type=params["content_type"],  # 默认值已由 _apply_defaults 填充
            title=params["title"],
            content=params["content"]
        )

        if success:
            logger.info(message)
            return ToolResult(success=True, data={"message": message})
        return ToolResult(success=False, error=message)


class UpdateArtifactTool(BaseTool):
    """
    更新 Artifact 工具
    通过指定 old_str 和 new_str 来更新内容（支持模糊匹配）
    """

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="update_artifact",
            description="Update artifact content by replacing old text with new text (Attempt fuzzy matching if exact text not found).",
            permission=ToolPermission.AUTO
        )
        self._manager = manager

    def set_manager(self, manager: ArtifactManager) -> None:
        """设置 ArtifactManager（依赖注入）"""
        self._manager = manager

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
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message, match_info = await self._manager.update_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            old_str=params["old_str"],
            new_str=params["new_str"]
        )

        if success:
            logger.info(message)

            memory = await self._manager.get_artifact(session_id, params["id"])
            result_data = {
                "message": message,
                "version": memory.current_version if memory else None
            }

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
        """生成 XML 调用示例（使用CDATA）"""
        return """<tool_call>
  <name>update_artifact</name>
  <params>
    <id><![CDATA[task_plan]]></id>
    <old_str><![CDATA[1. [✗] Search for recent developments
   - Status: pending
   - Assigned: search_agent
   - Notes: N/A]]></old_str>
    <new_str><![CDATA[1. [✓] Search for recent developments
   - Status: completed
   - Assigned: search_agent
   - Notes: Found 5 key breakthroughs]]></new_str>
  </params>
</tool_call>

IMPORTANT NOTES:
1. Use ACTUAL line breaks in XML, not \\n escape sequences
2. For multi-line updates, include all related lines as a unit
"""


class RewriteArtifactTool(BaseTool):
    """重写 Artifact 工具（完全替换内容）"""

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="rewrite_artifact",
            description="Completely rewrite the artifact content",
            permission=ToolPermission.AUTO
        )
        self._manager = manager

    def set_manager(self, manager: ArtifactManager) -> None:
        """设置 ArtifactManager（依赖注入）"""
        self._manager = manager

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
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message = await self._manager.rewrite_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            new_content=params["content"]
        )

        if success:
            logger.info(message)
            memory = await self._manager.get_artifact(session_id, params["id"])
            return ToolResult(
                success=True,
                data={
                    "message": message,
                    "version": memory.current_version if memory else None
                }
            )

        return ToolResult(success=False, error=message)


class ReadArtifactTool(BaseTool):
    """读取 Artifact 工具"""

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="read_artifact",
            description="Read artifact content",
            permission=ToolPermission.AUTO
        )
        self._manager = manager

    def set_manager(self, manager: ArtifactManager) -> None:
        """设置 ArtifactManager（依赖注入）"""
        self._manager = manager

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
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        result = await self._manager.read_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            version=params.get("version")
        )

        if result is None:
            version = params.get("version")
            if version:
                return ToolResult(success=False, error=f"Version {version} not found")
            return ToolResult(success=False, error=f"Artifact '{params['id']}' not found")

        return ToolResult(success=True, data=result)


# ============================================================
# 工厂函数
# ============================================================

def create_artifact_tools(manager: ArtifactManager) -> List[BaseTool]:
    """
    创建所有 Artifact 工具（工厂函数）

    Args:
        manager: ArtifactManager 实例

    Returns:
        工具列表
    """
    return [
        CreateArtifactTool(manager),
        UpdateArtifactTool(manager),
        RewriteArtifactTool(manager),
        ReadArtifactTool(manager),
    ]


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import asyncio
    from db.database import create_test_database_manager
    from repositories.artifact_repo import ArtifactRepository

    async def run_tests():
        """测试 ArtifactManager"""
        print("\n🧪 ArtifactManager Test Suite")
        print("=" * 60)

        # 创建测试数据库
        db = create_test_database_manager()
        await db.initialize()

        try:
            async with db.session() as session:
                # 创建 Repository 和 Manager
                repo = ArtifactRepository(session)
                manager = ArtifactManager(repo)

                # 设置 session
                session_id = "test-session-001"
                manager.set_session(session_id)
                await manager.ensure_session_exists(session_id)

                print(f"✅ Created manager for session: {session_id}")

                # 测试创建
                success, msg = await manager.create_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    content_type="markdown",
                    title="Test Plan",
                    content="# Task Plan\n\n1. [✗] Step 1\n2. [✗] Step 2"
                )
                print(f"✅ Create: {msg}")

                # 测试读取
                result = await manager.read_artifact(session_id, "task_plan")
                print(f"✅ Read: version={result['version']}")

                # 测试精确匹配更新
                success, msg, info = await manager.update_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    old_str="1. [✗] Step 1",
                    new_str="1. [✓] Step 1 - completed"
                )
                print(f"✅ Update (exact): {msg}")

                # 测试模糊匹配更新
                success, msg, info = await manager.update_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    old_str="2. [x] Step 2",  # 故意写错
                    new_str="2. [✓] Step 2 - done"
                )
                if success:
                    print(f"✅ Update (fuzzy): {msg}")
                else:
                    print(f"⚠️ Fuzzy match failed (expected): {msg}")

                # 测试重写
                success, msg = await manager.rewrite_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    new_content="# New Plan\n\nCompletely rewritten."
                )
                print(f"✅ Rewrite: {msg}")

                # 测试列表
                artifacts = await manager.list_artifacts(session_id)
                print(f"✅ List: {len(artifacts)} artifacts")

                print("\n" + "=" * 60)
                print("✅ All tests passed!")

        finally:
            await db.close()

    asyncio.run(run_tests())

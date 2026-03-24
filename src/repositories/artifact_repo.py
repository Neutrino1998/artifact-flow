"""
Artifact Repository

提供 Artifact 的 CRUD 操作和版本管理。
"""

from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Artifact,
    ArtifactSession,
    ArtifactVersion,
)
from repositories.base import BaseRepository, NotFoundError, DuplicateError


class ArtifactRepository(BaseRepository[Artifact]):
    """
    Artifact Repository

    职责：
    - Artifact 的 CRUD 操作
    - 版本管理
    - ArtifactSession 管理
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, Artifact)

    # ========================================
    # Session 操作
    # ========================================

    async def get_session(self, session_id: str) -> Optional[ArtifactSession]:
        """
        获取 ArtifactSession

        Args:
            session_id: Session ID（与 conversation_id 相同）

        Returns:
            ArtifactSession 对象，不存在则返回 None
        """
        return await self._session.get(ArtifactSession, session_id)

    async def get_session_or_raise(self, session_id: str) -> ArtifactSession:
        """
        获取 ArtifactSession（不存在则抛出异常）

        Args:
            session_id: Session ID

        Returns:
            ArtifactSession 对象

        Raises:
            NotFoundError: Session 不存在
        """
        art_session = await self.get_session(session_id)
        if not art_session:
            raise NotFoundError("ArtifactSession", session_id)
        return art_session

    async def ensure_session_exists(self, session_id: str) -> ArtifactSession:
        """
        确保 ArtifactSession 存在（不存在则创建）

        注意：通常 Session 会在创建 Conversation 时自动创建。
        此方法用于边缘情况的兼容性处理。

        Args:
            session_id: Session ID

        Returns:
            ArtifactSession 对象
        """
        art_session = await self.get_session(session_id)
        if not art_session:
            art_session = ArtifactSession(id=session_id)
            self._session.add(art_session)
            await self._session.flush()
            await self._session.commit()
        return art_session

    # ========================================
    # Artifact CRUD
    # ========================================

    async def create_artifact(
        self,
        session_id: str,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "agent",
        target_version: int = 1,
    ) -> Artifact:
        """
        创建 Artifact（同时创建初始版本）

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            content_type: 内容类型 (MIME type)
            title: 标题
            content: 初始内容
            metadata: 扩展元数据
            source: 来源 (agent, user_upload)
            target_version: 版本号（默认 1）。flush 折叠内存编辑时可传更大值。

        Returns:
            创建的 Artifact

        Raises:
            NotFoundError: Session 不存在
            DuplicateError: Artifact 已存在
        """
        await self.get_session_or_raise(session_id)

        existing = await self.get_artifact(session_id, artifact_id)
        if existing:
            raise DuplicateError("Artifact", f"{session_id}/{artifact_id}")

        artifact = Artifact(
            id=artifact_id,
            session_id=session_id,
            content_type=content_type,
            title=title,
            content=content,
            current_version=target_version,
            metadata_=metadata or {},
            source=source
        )

        self._session.add(artifact)

        version = ArtifactVersion(
            artifact_id=artifact_id,
            session_id=session_id,
            version=target_version,
            content=content,
            update_type="create",
            changes=None
        )

        self._session.add(version)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(artifact)

        return artifact

    async def get_artifact(
        self,
        session_id: str,
        artifact_id: str,
        *,
        load_versions: bool = False
    ) -> Optional[Artifact]:
        """
        获取 Artifact

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            load_versions: 是否预加载版本历史

        Returns:
            Artifact 对象，不存在则返回 None
        """
        query = select(Artifact).where(
            and_(
                Artifact.session_id == session_id,
                Artifact.id == artifact_id
            )
        )

        if load_versions:
            query = query.options(selectinload(Artifact.versions))

        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_artifact_or_raise(
        self,
        session_id: str,
        artifact_id: str,
        **kwargs
    ) -> Artifact:
        """
        获取 Artifact（不存在则抛出异常）

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            **kwargs: 传递给 get_artifact 的参数

        Returns:
            Artifact 对象

        Raises:
            NotFoundError: Artifact 不存在
        """
        artifact = await self.get_artifact(session_id, artifact_id, **kwargs)
        if not artifact:
            raise NotFoundError("Artifact", f"{session_id}/{artifact_id}")
        return artifact

    async def list_artifacts(
        self,
        session_id: str,
        *,
        content_type: Optional[str] = None,
    ) -> List[Artifact]:
        """
        列出 Session 的所有 Artifacts

        Args:
            session_id: Session ID
            content_type: 按类型筛选

        Returns:
            Artifact ORM 对象列表
        """
        query = select(Artifact).where(Artifact.session_id == session_id)

        if content_type:
            query = query.where(Artifact.content_type == content_type)

        query = query.order_by(Artifact.created_at)

        result = await self._session.execute(query)
        return list(result.scalars().all())

    # ========================================
    # 内容更新
    # ========================================

    async def upsert_artifact_content(
        self,
        session_id: str,
        artifact_id: str,
        new_content: str,
        update_type: str,
        changes: Optional[List[Tuple[str, str]]] = None,
        source: Optional[str] = None,
        target_version: Optional[int] = None,
    ) -> Artifact:
        """
        更新 Artifact 内容并创建新版本（无乐观锁）

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            new_content: 新内容
            update_type: 更新类型 (update/update_fuzzy/rewrite)
            changes: 变更记录 [(old, new), ...]
            source: 可选，更新来源（传入时同步更新 artifact.source）
            target_version: 显式指定版本号。None 则自增 1。
                            flush 传入内存版本号以保持一致。

        Returns:
            更新后的 Artifact

        Raises:
            NotFoundError: Artifact 不存在
        """
        artifact = await self.get_artifact_or_raise(session_id, artifact_id)

        new_ver = target_version if target_version is not None else artifact.current_version + 1
        artifact.content = new_content
        artifact.current_version = new_ver
        artifact.updated_at = datetime.now()
        if source is not None:
            artifact.source = source

        version = ArtifactVersion(
            artifact_id=artifact_id,
            session_id=session_id,
            version=new_ver,
            content=new_content,
            update_type=update_type,
            changes=changes
        )

        self._session.add(version)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(artifact)

        return artifact

    # ========================================
    # 版本管理
    # ========================================

    async def get_version(
        self,
        session_id: str,
        artifact_id: str,
        version: int
    ) -> Optional[ArtifactVersion]:
        """
        获取指定版本

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            version: 版本号

        Returns:
            ArtifactVersion 对象，不存在则返回 None
        """
        query = select(ArtifactVersion).where(
            and_(
                ArtifactVersion.session_id == session_id,
                ArtifactVersion.artifact_id == artifact_id,
                ArtifactVersion.version == version
            )
        )

        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_version_content(
        self,
        session_id: str,
        artifact_id: str,
        version: Optional[int] = None
    ) -> Optional[str]:
        """
        获取指定版本的内容

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            version: 版本号（None 则获取当前版本）

        Returns:
            版本内容字符串，不存在则返回 None
        """
        if version is None:
            artifact = await self.get_artifact(session_id, artifact_id)
            return artifact.content if artifact else None
        else:
            ver = await self.get_version(session_id, artifact_id, version)
            return ver.content if ver else None

    async def list_versions(
        self,
        session_id: str,
        artifact_id: str
    ) -> List[ArtifactVersion]:
        """
        列出 Artifact 的所有版本

        Args:
            session_id: Session ID
            artifact_id: Artifact ID

        Returns:
            ArtifactVersion ORM 对象列表（按版本号升序）
        """
        query = (
            select(ArtifactVersion)
            .where(
                and_(
                    ArtifactVersion.session_id == session_id,
                    ArtifactVersion.artifact_id == artifact_id
                )
            )
            .order_by(ArtifactVersion.version)
        )

        result = await self._session.execute(query)
        return list(result.scalars().all())

"""
Artifact Repository

提供 Artifact 的 CRUD 操作、版本管理和乐观锁实现。
"""

from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Artifact, 
    ArtifactSession, 
    ArtifactVersion,
    VersionConflictError
)
from repositories.base import BaseRepository, NotFoundError, DuplicateError


class ArtifactRepository(BaseRepository[Artifact]):
    """
    Artifact Repository
    
    职责：
    - Artifact 的 CRUD 操作
    - 版本管理
    - 乐观锁实现（防止并发冲突）
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
            ArtifactSession 对象
        """
        return await self._session.get(ArtifactSession, session_id)
    
    async def get_session_or_raise(self, session_id: str) -> ArtifactSession:
        """
        获取 ArtifactSession（不存在则抛出异常）
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
        metadata: Optional[Dict[str, Any]] = None
    ) -> Artifact:
        """
        创建 Artifact（同时创建初始版本）
        
        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            content_type: 内容类型 (markdown/python/etc)
            title: 标题
            content: 初始内容
            metadata: 扩展元数据
            
        Returns:
            创建的 Artifact
            
        Raises:
            NotFoundError: Session 不存在
            DuplicateError: Artifact 已存在
        """
        # 确保 Session 存在
        await self.get_session_or_raise(session_id)
        
        # 检查 Artifact 是否已存在
        existing = await self.get_artifact(session_id, artifact_id)
        if existing:
            raise DuplicateError("Artifact", f"{session_id}/{artifact_id}")
        
        # 创建 Artifact
        artifact = Artifact(
            id=artifact_id,
            session_id=session_id,
            content_type=content_type,
            title=title,
            content=content,
            current_version=1,
            lock_version=1,
            metadata_=metadata or {}
        )
        
        self._session.add(artifact)

        # 创建初始版本
        version = ArtifactVersion(
            artifact_id=artifact_id,
            session_id=session_id,
            version=1,
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
        include_content: bool = True,
        content_preview_length: int = 200
    ) -> List[Dict[str, Any]]:
        """
        列出 Session 的所有 Artifacts
        
        Args:
            session_id: Session ID
            content_type: 按类型筛选
            include_content: 是否包含内容
            content_preview_length: 内容预览长度
            
        Returns:
            Artifact 信息列表
        """
        query = select(Artifact).where(Artifact.session_id == session_id)
        
        if content_type:
            query = query.where(Artifact.content_type == content_type)
        
        query = query.order_by(Artifact.created_at)
        
        result = await self._session.execute(query)
        artifacts = result.scalars().all()
        
        artifact_list = []
        for artifact in artifacts:
            info = {
                "id": artifact.id,
                "content_type": artifact.content_type,
                "title": artifact.title,
                "version": artifact.current_version,
                "lock_version": artifact.lock_version,
                "updated_at": artifact.updated_at.isoformat()
            }
            
            if include_content:
                content = artifact.content
                if len(content) > content_preview_length:
                    info["content"] = content[:content_preview_length] + "[...]"
                else:
                    info["content"] = content
            
            artifact_list.append(info)
        
        return artifact_list
    
    async def delete_artifact(
        self,
        session_id: str,
        artifact_id: str
    ) -> bool:
        """
        删除 Artifact（级联删除所有版本）
        
        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            
        Returns:
            是否成功删除
        """
        artifact = await self.get_artifact(session_id, artifact_id)
        if not artifact:
            return False
        
        await self._session.delete(artifact)
        await self._session.flush()
        await self._session.commit()
        return True
    
    # ========================================
    # 乐观锁更新
    # ========================================
    
    async def update_artifact_content(
        self,
        session_id: str,
        artifact_id: str,
        new_content: str,
        update_type: str,
        expected_lock_version: int,
        changes: Optional[List[Tuple[str, str]]] = None
    ) -> Artifact:
        """
        更新 Artifact 内容（带乐观锁）
        
        这是核心的更新方法，使用乐观锁防止并发冲突。
        
        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            new_content: 新内容
            update_type: 更新类型 (update/update_fuzzy/rewrite)
            expected_lock_version: 预期的锁版本（用于乐观锁检查）
            changes: 变更记录 [(old, new), ...]
            
        Returns:
            更新后的 Artifact
            
        Raises:
            NotFoundError: Artifact 不存在
            VersionConflictError: 版本冲突
        """
        # 使用原子更新操作
        result = await self._session.execute(
            update(Artifact)
            .where(
                and_(
                    Artifact.id == artifact_id,
                    Artifact.session_id == session_id,
                    Artifact.lock_version == expected_lock_version  # 乐观锁检查
                )
            )
            .values(
                content=new_content,
                current_version=Artifact.current_version + 1,
                lock_version=Artifact.lock_version + 1,
                updated_at=datetime.now()
            )
            .returning(Artifact.current_version, Artifact.lock_version)
        )
        
        row = result.first()
        
        if row is None:
            # 更新失败：可能是不存在或版本冲突
            artifact = await self.get_artifact(session_id, artifact_id)
            if not artifact:
                raise NotFoundError("Artifact", f"{session_id}/{artifact_id}")
            else:
                raise VersionConflictError(
                    f"Version conflict: expected lock_version={expected_lock_version}, "
                    f"actual={artifact.lock_version}",
                    artifact_id=artifact_id,
                    expected_version=expected_lock_version
                )
        
        new_version_num = row[0]
        
        # 创建版本记录
        version = ArtifactVersion(
            artifact_id=artifact_id,
            session_id=session_id,
            version=new_version_num,
            content=new_content,
            update_type=update_type,
            changes=changes
        )
        
        self._session.add(version)
        await self._session.flush()
        await self._session.commit()

        # 重新加载 Artifact
        artifact = await self.get_artifact(session_id, artifact_id)
        return artifact
    
    async def rewrite_artifact(
        self,
        session_id: str,
        artifact_id: str,
        new_content: str,
        expected_lock_version: int
    ) -> Artifact:
        """
        完全重写 Artifact 内容
        
        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            new_content: 新内容
            expected_lock_version: 预期的锁版本
            
        Returns:
            更新后的 Artifact
        """
        return await self.update_artifact_content(
            session_id=session_id,
            artifact_id=artifact_id,
            new_content=new_content,
            update_type="rewrite",
            expected_lock_version=expected_lock_version,
            changes=None
        )
    
    async def update_artifact_title(
        self,
        session_id: str,
        artifact_id: str,
        new_title: str
    ) -> Artifact:
        """
        更新 Artifact 标题（不需要乐观锁）
        
        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            new_title: 新标题
            
        Returns:
            更新后的 Artifact
        """
        artifact = await self.get_artifact_or_raise(session_id, artifact_id)
        artifact.title = new_title
        artifact.updated_at = datetime.now()
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
            ArtifactVersion 对象
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
            版本内容
        """
        if version is None:
            # 获取当前版本
            artifact = await self.get_artifact(session_id, artifact_id)
            return artifact.content if artifact else None
        else:
            # 获取历史版本
            ver = await self.get_version(session_id, artifact_id, version)
            return ver.content if ver else None
    
    async def list_versions(
        self,
        session_id: str,
        artifact_id: str
    ) -> List[Dict[str, Any]]:
        """
        列出 Artifact 的所有版本
        
        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            
        Returns:
            版本信息列表（不含完整内容）
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
        versions = result.scalars().all()
        
        return [
            {
                "version": v.version,
                "update_type": v.update_type,
                "created_at": v.created_at.isoformat(),
                "has_changes": v.changes is not None,
                "change_count": len(v.changes) if v.changes else 0
            }
            for v in versions
        ]
    
    async def get_version_diff(
        self,
        session_id: str,
        artifact_id: str,
        from_version: int,
        to_version: int
    ) -> Optional[Dict[str, Any]]:
        """
        获取两个版本之间的差异信息
        
        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            from_version: 起始版本
            to_version: 目标版本
            
        Returns:
            差异信息字典，包含两个版本的内容
        """
        from_ver = await self.get_version(session_id, artifact_id, from_version)
        to_ver = await self.get_version(session_id, artifact_id, to_version)
        
        if not from_ver or not to_ver:
            return None
        
        return {
            "from_version": from_version,
            "to_version": to_version,
            "from_content": from_ver.content,
            "to_content": to_ver.content,
            "to_update_type": to_ver.update_type,
            "to_changes": to_ver.changes
        }
    
    # ========================================
    # 批量操作
    # ========================================
    
    async def clear_temporary_artifacts(
        self,
        session_id: str,
        temporary_ids: Optional[List[str]] = None
    ) -> int:
        """
        清除临时 Artifacts
        
        Args:
            session_id: Session ID
            temporary_ids: 临时 Artifact ID 列表（默认 ["task_plan"]）
            
        Returns:
            删除的数量
        """
        if temporary_ids is None:
            temporary_ids = ["task_plan"]
        
        deleted_count = 0
        for artifact_id in temporary_ids:
            if await self.delete_artifact(session_id, artifact_id):
                deleted_count += 1
        
        return deleted_count
    
    async def get_artifacts_with_full_content(
        self,
        session_id: str,
        artifact_ids: List[str]
    ) -> Dict[str, Artifact]:
        """
        批量获取 Artifacts（含完整内容）
        
        Args:
            session_id: Session ID
            artifact_ids: Artifact ID 列表
            
        Returns:
            {artifact_id: Artifact} 字典
        """
        if not artifact_ids:
            return {}
        
        query = select(Artifact).where(
            and_(
                Artifact.session_id == session_id,
                Artifact.id.in_(artifact_ids)
            )
        )
        
        result = await self._session.execute(query)
        artifacts = result.scalars().all()
        
        return {a.id: a for a in artifacts}

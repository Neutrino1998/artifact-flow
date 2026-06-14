"""
Artifact Repository

提供 Artifact 的 CRUD 操作和版本管理。
"""

from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Artifact,
    ArtifactBlob,
    ArtifactSession,
    ArtifactVersion,
    Conversation,
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
        blob: Optional[bytes] = None,
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
            blob: 可选的二进制源字节。提供时在**同一事务**写入 ArtifactBlob 行
                  (与 artifact + version 原子),保证「artifact 在则 blob 在」。

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

        if blob is not None:
            self._session.add(ArtifactBlob(
                artifact_id=artifact_id,
                session_id=session_id,
                data=blob,
                size_bytes=len(blob),
            ))

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

        # Ordering contract: `(created_at, id)`.
        #
        # `created_at` is `server_default=func.now()`. On SQLite that's
        # second-resolution, so multiple INSERTs within the same `flush_all()`
        # collide on `created_at` and the engine's own tiebreaker becomes
        # implementation-defined (and can leak PYTHONHASHSEED through the
        # iteration order of the `_dirty` flush set above). The `id` secondary
        # key removes that nondeterminism.
        #
        # **Caveat (intentional limitation):** this stabilizes the order across
        # runs but does NOT strictly preserve creation order when `created_at`
        # ties. If two artifacts created in the same second have ids that do not
        # sort in creation order (e.g. `b_doc` then `a_doc`), `list_artifacts()`
        # returns them as `a_doc, b_doc`. Session-wide consumers (`grep_artifact`
        # session cap, etc.) get a stable subset across runs, but the subset may
        # differ from the pre-flush in-memory order. Accepted trade-off:
        # preserving strict creation order across flush would need either an
        # explicit `creation_seq` column (Alembic migration) or app-side
        # `created_at` assignment (which conflicts with CLAUDE.md's
        # "server_default for creation" rule).
        query = query.order_by(Artifact.created_at, Artifact.id)

        result = await self._session.execute(query)
        return list(result.scalars().all())

    # ========================================
    # 二进制存储 (ArtifactBlob)
    # ========================================

    async def get_blob(
        self,
        session_id: str,
        artifact_id: str,
    ) -> Optional[ArtifactBlob]:
        """获取 artifact 的二进制 blob（含字节）。

        **仅 raw-fetch 路径调用**：显式 SELECT 而非走 `Artifact.blob` 关系，使
        「载入 MB 级字节」永远是一次有意的调用，杜绝任何热路径(list/inventory/
        get_artifact)经关系误触发字节载入。
        """
        query = select(ArtifactBlob).where(
            and_(
                ArtifactBlob.session_id == session_id,
                ArtifactBlob.artifact_id == artifact_id,
            )
        )
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    # ========================================
    # 存储配额聚合（只读 size_bytes 冗余列，绝不载入 data）
    # ========================================

    async def get_user_blob_bytes(self, user_id: str) -> int:
        """该用户名下所有 artifact blob 的总字节数（跨其全部 conversation/session）。

        用于上传准入配额检查 + 进度条总量。join Conversation 取 user_id（
        ArtifactBlob.session_id == Conversation.id，session_id 与 conversation_id 同）。
        只聚合 size_bytes（走 ix_artifact_blobs_session_size，index-only），不触 data。
        SUM 在 PG 升 bigint / SQLite 任意精度，跨用户总量不受列的 Integer 上限制约。
        """
        stmt = (
            select(func.coalesce(func.sum(ArtifactBlob.size_bytes), 0))
            .select_from(ArtifactBlob)
            .join(Conversation, Conversation.id == ArtifactBlob.session_id)
            .where(Conversation.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_user_blob_bytes_for_session(self, session_id: str) -> int:
        """该 session 属主已占用的 blob 总字节（跨属主全部会话），**一趟查询**。

        写入侧 chokepoint（create_from_upload）只有 session_id，配额却是 per-user 跨
        全部会话；子查询内联解析属主，免去额外一趟 owner 查询。无主会话 → 子查询
        NULL → `user_id = NULL` 匹配不到 → 返回 0（单个超大 blob 仍由数值判定拦下）。
        只读 size_bytes，走 ix_artifact_blobs_session_size（index-only）。
        """
        owner = (
            select(Conversation.user_id)
            .where(Conversation.id == session_id)
            .scalar_subquery()
        )
        stmt = (
            select(func.coalesce(func.sum(ArtifactBlob.size_bytes), 0))
            .select_from(ArtifactBlob)
            .join(Conversation, Conversation.id == ArtifactBlob.session_id)
            .where(Conversation.user_id == owner)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_blob_bytes_by_sessions(self, session_ids: List[str]) -> Dict[str, int]:
        """一批 session 各自的 blob 总字节（GROUP BY）。

        用于会话列表逐项展示"附件占用"。无 blob 的 session 不出现在结果里 ——
        调用方用 `.get(id, 0)` 兜 0。空入参短路返回 {}。
        """
        if not session_ids:
            return {}
        stmt = (
            select(
                ArtifactBlob.session_id,
                func.coalesce(func.sum(ArtifactBlob.size_bytes), 0),
            )
            .where(ArtifactBlob.session_id.in_(session_ids))
            .group_by(ArtifactBlob.session_id)
        )
        result = await self._session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

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

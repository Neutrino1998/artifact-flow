"""Skill 数据访问：可见性读取 + 导入/删除写入。

三层职责模型的 Repository 层:只取数、不做业务/格式化,ORM 不外逃(返回标量 /
plain dict / set)。可见性解析(EffectiveSkillSet)、CRUD 编排在上层 Manager。
"""

from typing import Dict, Optional

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DepartmentSkillRule, Skill, User, UserSkill
from repositories.base import DuplicateError


class SkillRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def user_department_id(self, user_id: str) -> Optional[str]:
        """用户当前部门(从 DB 取,不信 JWT —— dept 授权是 correctness)。"""
        return (
            await self._session.execute(
                select(User.department_id).where(User.id == user_id)
            )
        ).scalar_one_or_none()

    async def user_overrides(self, user_id: str) -> Dict[str, bool]:
        """该用户的 user_skill 稀疏覆盖 `{skill_id: enabled}`。"""
        rows = (
            await self._session.execute(
                select(UserSkill.skill_id, UserSkill.enabled).where(
                    UserSkill.user_id == user_id
                )
            )
        ).all()
        return {skill_id: enabled for skill_id, enabled in rows}

    async def get_skill_md(self, skill_id: str) -> Optional[str]:
        """L2 read_skill 的正文取数(标量,不外逃 ORM)。"""
        return (
            await self._session.execute(
                select(Skill.skill_md).where(Skill.id == skill_id)
            )
        ).scalar_one_or_none()

    async def get_bundle(self, skill_id: str) -> Optional[bytes]:
        """skill zip 包取数(标量,不外逃 ORM)。无此 skill → None。"""
        return (
            await self._session.execute(
                select(Skill.bundle).where(Skill.id == skill_id)
            )
        ).scalar_one_or_none()

    async def get_user_bundle_bytes(self, user_id: str) -> int:
        """该用户私有 skill bundle 的总字节（导入配额记账）。与 artifact blob 共用
        一个池(config.ARTIFACT_USER_QUOTA_BYTES),聚合口径在 ConversationManager.
        get_user_upload_bytes —— 此处只出 skill 一侧的加数。"""
        return int((
            await self._session.execute(
                select(func.coalesce(func.sum(func.length(Skill.bundle)), 0)).where(
                    Skill.owner_user_id == user_id, Skill.bundle.isnot(None)
                )
            )
        ).scalar_one())

    async def lock_user_for_private_import(self, user_id: str) -> bool:
        """Lock one user's row for the private-skill count-and-insert transaction.

        Distinct users remain independent. The stable parent row also gives an empty
        collection something to lock, unlike locking the user's existing skill rows.

        SQLite ignores ``FOR UPDATE``. A no-op write acquires its single-writer lock
        before the count, serializing the later count-and-insert across connections.
        Raw SQL intentionally bypasses ``User.updated_at``'s SQLAlchemy ``onupdate``.
        """
        if self._session.get_bind().dialect.name == "sqlite":
            result = await self._session.execute(
                text("UPDATE users SET id = id WHERE id = :user_id"),
                {"user_id": user_id},
            )
            return result.rowcount == 1

        return (
            await self._session.execute(
                select(User.id).where(User.id == user_id).with_for_update()
            )
        ).scalar_one_or_none() is not None

    async def count_owned_skills(self, user_id: str) -> int:
        """Count one user's private skills using a locking/current read.

        `get_current_user` already performed a normal read in this request session. On
        MySQL REPEATABLE READ, a later plain COUNT could therefore retain a snapshot
        from before a competing import committed, even after the user-row lock waited.
        Selecting the owned rows FOR UPDATE forces a current read on MySQL/PostgreSQL;
        the collection is deliberately tiny because this is the configured count cap.
        """
        rows = (
            await self._session.execute(
                select(Skill.id)
                .where(Skill.owner_user_id == user_id)
                .with_for_update()
            )
        ).scalars().all()
        return len(rows)

    async def get_skill_row_meta(self, skill_id: str) -> Optional[dict]:
        """skill_id → 行级元数据(admin 删除路径用,绕过可见性;不存在 → None)。"""
        row = (
            await self._session.execute(
                select(
                    Skill.id, Skill.slug, Skill.source, Skill.owner_user_id, Skill.visibility
                ).where(Skill.id == skill_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return {
            "id": row.id, "slug": row.slug, "source": row.source,
            "owner_user_id": row.owner_user_id, "visibility": row.visibility,
        }

    async def list_shared_skills(self) -> list[Skill]:
        """Admin shared catalog: public/department skills with no owner."""
        return list((
            await self._session.execute(
                select(Skill)
                .where(
                    Skill.owner_user_id.is_(None),
                    Skill.visibility.in_(["public", "department"]),
                )
                .order_by(Skill.slug)
            )
        ).scalars().all())

    async def get_skill_for_update(self, skill_id: str) -> Optional[Skill]:
        """Load and row-lock a skill for writes that interpret or change visibility."""
        return (
            await self._session.execute(
                select(Skill).where(Skill.id == skill_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def get_shared_skill(self, identifier: str, *, for_update: bool = False) -> Optional[Skill]:
        """Resolve one shared skill by stable id, falling back to its shared-unique slug."""
        statement = select(Skill).where(
            Skill.id == identifier,
            Skill.owner_user_id.is_(None),
            Skill.visibility.in_(["public", "department"]),
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is not None:
            return row
        statement = select(Skill).where(
            Skill.slug == identifier,
            Skill.owner_user_id.is_(None),
            Skill.visibility.in_(["public", "department"]),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_shared_skill_detail(self, identifier: str) -> Optional[dict]:
        """Shared-catalog preview projection, excluding the potentially large bundle."""
        columns = (
            Skill.id,
            Skill.slug,
            Skill.name,
            Skill.description,
            Skill.skill_md,
            Skill.source,
            Skill.visibility,
            Skill.has_extra_files,
        )
        filters = (
            Skill.owner_user_id.is_(None),
            Skill.visibility.in_(["public", "department"]),
        )
        row = (
            await self._session.execute(
                select(*columns).where(Skill.id == identifier, *filters)
            )
        ).one_or_none()
        if row is None:
            row = (
                await self._session.execute(
                    select(*columns).where(Skill.slug == identifier, *filters)
                )
            ).one_or_none()
        return dict(row._mapping) if row is not None else None

    async def scope_slug_exists(self, slug: str, owner_user_id: Optional[str]) -> bool:
        namespace_key = owner_user_id or ""
        return (
            await self._session.execute(
                select(Skill.id).where(
                    Skill.namespace_key == namespace_key,
                    Skill.slug == slug,
                )
            )
        ).scalar_one_or_none() is not None

    async def insert_skill(self, **fields) -> None:
        """Insert and commit one skill, normalizing concurrent slug collisions."""
        self._session.add(Skill(**fields))
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DuplicateError("Skill", fields.get("slug")) from exc

    async def delete_skill(self, skill_id: str) -> None:
        """Delete one skill; related rules disappear through FK cascades."""
        await self._session.execute(delete(Skill).where(Skill.id == skill_id))
        await self._session.commit()

    async def clear_dept_rules(self, skill_id: str) -> None:
        """Clear department rules for one skill when its visibility changes."""
        await self._session.execute(
            delete(DepartmentSkillRule).where(DepartmentSkillRule.skill_id == skill_id)
        )

    async def commit_changes(self) -> None:
        """Commit mutations of rows loaded by this repository."""
        await self._session.commit()

    async def set_user_override(self, user_id: str, skill_id: str, enabled: bool) -> None:
        """Upsert and commit one sparse personal enable/disable override.

        SELECT→INSERT 非原子:两请求(两标签页/重试客户端)同用户同 skill 首次并发 toggle 会
        都读到 None、都 insert → 后者撞复合 PK IntegrityError。捕获 → rollback → 重读改 UPDATE
        (last-writer-wins),把并发首插的自我 500 收成正常写(镜像 ToolRegistryManager._commit)。

        **调用约束**:冲突路径 `rollback()` 会回滚**整个 session** —— 故本方法必须在一个
        use-case 里**先于任何其它 staged 写**调用(今唯一调用者 SkillManager.set_enabled 之前
        只有读,安全)。若未来有调用者在它之前 stage 了别的写,那些写会在冲突时被静默丢掉。"""
        async def _apply() -> bool:
            """有行则 UPDATE 返 True;无行则 stage INSERT 返 False(供撞 PK 时区分处理)。"""
            row = (
                await self._session.execute(
                    select(UserSkill).where(
                        UserSkill.user_id == user_id, UserSkill.skill_id == skill_id
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.enabled = enabled
                return True
            self._session.add(
                UserSkill(user_id=user_id, skill_id=skill_id, enabled=enabled)
            )
            return False

        await _apply()
        try:
            await self._session.flush()
        except IntegrityError:
            # 并发首插竞态:对方已插同 PK。回滚本次 staged insert,重读改 UPDATE。
            await self._session.rollback()
            await _apply()
            await self._session.flush()
        await self._session.commit()

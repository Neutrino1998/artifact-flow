"""Skill 数据访问(纯读 C-2 + 导入/删除写 E-2)。

三层职责模型的 Repository 层:只取数、不做业务/格式化,ORM 不外逃(返回标量 /
plain dict / set)。可见性解析(EffectiveSkillSet)、CRUD 编排在上层 Manager。
"""

from typing import Dict, List, Optional, Set

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DepartmentSkillRule, Skill, User, UserSkill


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
        """该用户的 user_skill 稀疏覆盖 `{slug: enabled}`。"""
        rows = (
            await self._session.execute(
                select(UserSkill.skill_slug, UserSkill.enabled).where(
                    UserSkill.user_id == user_id
                )
            )
        ).all()
        return {slug: enabled for slug, enabled in rows}

    async def dept_matched_slugs(self, dept_ids: List[str]) -> Set[str]:
        """祖先链中任一部门有 department_skill_rule 例外的 skill slug 集(方向由 visibility 派生)。"""
        if not dept_ids:
            return set()
        rows = (
            await self._session.execute(
                select(DepartmentSkillRule.skill_slug).where(
                    DepartmentSkillRule.department_id.in_(dept_ids)
                )
            )
        ).scalars().all()
        return set(rows)

    async def get_skill_md(self, slug: str) -> Optional[str]:
        """L2 read_skill 的正文取数(标量,不外逃 ORM)。"""
        return (
            await self._session.execute(
                select(Skill.skill_md).where(Skill.slug == slug)
            )
        ).scalar_one_or_none()

    async def get_bundle(self, slug: str) -> Optional[bytes]:
        """skill zip 包取数(标量,不外逃 ORM)。无此 skill → None。"""
        return (
            await self._session.execute(
                select(Skill.bundle).where(Skill.slug == slug)
            )
        ).scalar_one_or_none()

    async def get_user_bundle_bytes(self, user_id: str) -> int:
        """该用户私有 skill bundle 的总字节(导入配额记账,E-2)。与 artifact blob 共用
        一个池(config.ARTIFACT_USER_QUOTA_BYTES),聚合口径在 ConversationManager.
        get_user_upload_bytes —— 此处只出 skill 一侧的加数。"""
        return int((
            await self._session.execute(
                select(func.coalesce(func.sum(func.length(Skill.bundle)), 0)).where(
                    Skill.owner_user_id == user_id, Skill.bundle.isnot(None)
                )
            )
        ).scalar_one())

    async def get_skill_row_meta(self, slug: str) -> Optional[dict]:
        """slug → 行级元数据(admin 删除路径用,绕过可见性;不存在 → None)。"""
        row = (
            await self._session.execute(
                select(Skill.slug, Skill.source, Skill.owner_user_id, Skill.visibility)
                .where(Skill.slug == slug)
            )
        ).one_or_none()
        if row is None:
            return None
        return {
            "slug": row.slug, "source": row.source,
            "owner_user_id": row.owner_user_id, "visibility": row.visibility,
        }

    async def slug_exists(self, slug: str) -> bool:
        return (
            await self._session.execute(select(Skill.slug).where(Skill.slug == slug))
        ).scalar_one_or_none() is not None

    def stage_insert_skill(self, **fields) -> None:
        """stage 一行新 skill(commit 归 Manager;并发撞 slug 由 commit 的
        IntegrityError 暴露,Manager 折成 409)。"""
        self._session.add(Skill(**fields))

    async def delete_skill(self, slug: str) -> None:
        """stage 删除(commit 归 Manager)。user_skill / dept 规则由 DB FK CASCADE 清,
        零 app-side 清理。"""
        await self._session.execute(delete(Skill).where(Skill.slug == slug))

    async def set_user_override(self, user_id: str, slug: str, enabled: bool) -> None:
        """Upsert user_skill 稀疏覆盖行(个人 enable/disable)。stage-only,commit 归 Manager
        (事务边界 = 每个 use-case,同 ToolRegistryManager)。

        SELECT→INSERT 非原子:两请求(两标签页/重试客户端)同用户同 slug 首次并发 toggle 会
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
                        UserSkill.user_id == user_id, UserSkill.skill_slug == slug
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.enabled = enabled
                return True
            self._session.add(
                UserSkill(user_id=user_id, skill_slug=slug, enabled=enabled)
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

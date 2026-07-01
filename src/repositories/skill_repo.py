"""Skill 数据访问(纯读,Phase C-2)。

三层职责模型的 Repository 层:只取数、不做业务/格式化,ORM 不外逃(返回标量 /
plain dict / set)。可见性解析(EffectiveSkillSet)、CRUD(C-3 Manager)在上层。
"""

from typing import Dict, List, Optional, Set

from sqlalchemy import select
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
        """L3 mount_skill 的 bundle 取数(完整原始 zip 字节,标量,不外逃 ORM)。
        无此 skill 或单文件 skill(bundle NULL)均返回 None —— 调用方(mount_skill)据此
        分别报「不可见」/「无 bundle 可挂」。"""
        return (
            await self._session.execute(
                select(Skill.bundle).where(Skill.slug == slug)
            )
        ).scalar_one_or_none()

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

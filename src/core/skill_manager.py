"""SkillManager —— 用户侧 skill 列举 + 个人 enable/disable 覆盖的用例编排(C-3)。

三层中的 Manager:经 EffectiveSkillSet 做可见性闸(visible=正确性,miss→404 不泄露存在性),
序列化成前端列表,个人 toggle 写 user_skill 稀疏覆盖。router 只做 transport(认证/HTTP 映射)。

作用域守 feedback-admin-scope-user-mgmt:这是**用户自己的** skill 偏好(个人 opt-in),非
admin 管共享资源。seeded skill 的 visibility/default_enabled 归 config 只读 —— 用户能改的只有
自己的 enabled 覆盖(不进 L1 ≠ 不可见:关掉的 skill 仍可用按钮显式激活)。动态 skill CRUD +
dept 授权 UI 留后续阶段(E/G)。

事务边界 = 每个 use-case:Manager 持 session、调 stage-only repo 后一次 commit(同
ToolRegistryManager;单写用例无需跨 repo 原子性)。
"""

from typing import Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from core.department_resolver import load_ancestor_ids
from core.effective_skillset import EffectiveSkillSet, resolve_effective_skillset
from reconcile.snapshot import load_skill_snapshot
from repositories.skill_repo import SkillRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class SkillManagerError(Exception):
    """skill 管理业务错误基类;status_code 供 router 映射 HTTP。"""
    status_code = 400


class SkillNotFoundError(SkillManagerError):
    status_code = 404


class SkillManager:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = SkillRepository(session)

    async def _resolve(self, user_id: str) -> Tuple[EffectiveSkillSet, Dict[str, bool]]:
        """解析该用户的 EffectiveSkillSet + user_overrides(列举/toggle 复用,同
        controller_factory._load_skills 的口径 —— 单点可见性,杜绝注入有闸/管理没闸漂移)。"""
        snapshot = await load_skill_snapshot(self._session)
        dept_id = await self._repo.user_department_id(user_id)
        ancestors = await load_ancestor_ids(self._session, dept_id)
        overrides = await self._repo.user_overrides(user_id)
        dept_matched = await self._repo.dept_matched_slugs(ancestors)
        eff = resolve_effective_skillset(user_id, snapshot, overrides, dept_matched)
        return eff, overrides

    @staticmethod
    def _serialize(info, *, enabled: bool, is_overridden: bool) -> dict:
        return {
            "slug": info.slug,
            "name": info.name,
            "description": info.description,
            "enabled": enabled,                       # 有效态(覆盖后,决定进不进 L1)
            "default_enabled": info.default_enabled,  # 系统默认(区分是否被个人改过)
            "is_overridden": is_overridden,
        }

    async def list_for_user(self, user_id: str) -> List[dict]:
        """列出该用户**可见**的 skill + 有效启用态(供设置页;保 snapshot 的 slug 顺序)。"""
        eff, overrides = await self._resolve(user_id)
        return [
            self._serialize(
                info, enabled=slug in eff.enabled, is_overridden=slug in overrides
            )
            for slug, info in eff.visible.items()
        ]

    async def set_enabled(self, user_id: str, slug: str, enabled: bool) -> dict:
        """个人 enable/disable(写 user_skill 覆盖)。不可见 → 404(不泄露存在性)。"""
        eff, _ = await self._resolve(user_id)
        info = eff.visible.get(slug)
        if info is None:
            raise SkillNotFoundError(f"skill '{slug}' not found")
        await self._repo.set_user_override(user_id, slug, enabled)
        await self._session.commit()
        return self._serialize(info, enabled=enabled, is_overridden=True)

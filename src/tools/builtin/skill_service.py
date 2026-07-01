"""SkillService —— skill 正文取数(B-5 短 session lazy 读)。

read_skill 工具的协作者:持 db_manager(非绑一条 turn-long session),execute 期按 slug
开一条短 retrying session 取 skill_md 读完即关(同 ArtifactService / CredentialResolver)。
"""

from typing import Optional

from repositories.skill_repo import SkillRepository


class SkillService:
    def __init__(self, db_manager=None):
        self._db_manager = db_manager

    async def get_skill_md(self, slug: str) -> Optional[str]:
        """取 skill 正文(L2)。无 db_manager(测试)→ None。"""
        if self._db_manager is None:
            return None
        return await self._db_manager.with_retry(
            lambda session: SkillRepository(session).get_skill_md(slug)
        )

    async def get_bundle(self, slug: str) -> Optional[bytes]:
        """取 skill bundle(L3,完整原始 zip 字节)。无 db_manager(测试)/ 无 bundle → None。"""
        if self._db_manager is None:
            return None
        return await self._db_manager.with_retry(
            lambda session: SkillRepository(session).get_bundle(slug)
        )

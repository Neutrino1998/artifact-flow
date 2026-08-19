"""SkillService —— skill 正文按需取数。

read_skill 工具的协作者:持 db_manager(非绑一条 turn-long session),execute 期按 skill_id
开一条短 retrying session 取 skill_md 读完即关(同 ArtifactService / CredentialResolver)。
"""

from typing import Optional

from repositories.skill_repo import SkillRepository


class SkillService:
    def __init__(self, db_manager):
        if db_manager is None:
            raise ValueError("SkillService requires a db_manager")
        self._db_manager = db_manager

    async def get_skill_md(self, skill_id: str) -> Optional[str]:
        """取 skill 正文(L2)；不存在时返回 None。"""
        return await self._db_manager.with_retry(
            lambda session: SkillRepository(session).get_skill_md(skill_id)
        )

    async def get_bundle(self, skill_id: str) -> Optional[bytes]:
        """取 skill bundle(L3,完整 zip 字节)；不存在时返回 None。"""
        return await self._db_manager.with_retry(
            lambda session: SkillRepository(session).get_bundle(skill_id)
        )

"""
ToolRegistry Repository(写侧)—— tool_units / tool_members / agent_units 的数据访问。

纯数据访问、**不 commit**(事务边界归 ToolRegistryManager 的 use-case):staging 写 +
查询。读侧的运行期快照另在 reconcile/snapshot.py(那里只读、重建运行形状)。
ORM 实例不外逃:Manager 在同 session 内读其列做序列化,不把对象交给 router。
"""

from typing import Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Agent, AgentUnit, ToolCredential, ToolMember, ToolUnit
from repositories.base import BaseRepository


class ToolRegistryRepository(BaseRepository[ToolUnit]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, ToolUnit)

    # ---- 读 ----------------------------------------------------------------

    async def list_units(self) -> List[ToolUnit]:
        """全部 unit(members 经 selectin 预载)。external 数量级小,全量拉。"""
        return list((await self._session.execute(
            select(ToolUnit).order_by(ToolUnit.name)
        )).scalars().all())

    async def get_unit(self, name: str) -> Optional[ToolUnit]:
        # 用 select(非 session.get):commit 后 expire_on_commit 让 identity-map 实例过期,
        # session.get 返回它但 `u.members`(selectin)会在 await 外触发 lazy IO → MissingGreenlet。
        # select 语句执行时 selectin post-load 在 await 内跑,members 即时加载。
        return (await self._session.execute(
            select(ToolUnit).where(ToolUnit.name == name)
        )).scalar_one_or_none()

    async def existing_full_names(self, exclude_unit: Optional[str] = None) -> Dict[str, str]:
        """{full_name: unit_name},排除某 unit(update 时排自身)。撞名 by-construction 闸用。"""
        rows = (await self._session.execute(
            select(ToolMember.full_name, ToolMember.unit_name)
        )).all()
        return {fn: un for fn, un in rows if un != exclude_unit}

    async def existing_unit_names(self) -> set:
        return set((await self._session.execute(select(ToolUnit.name))).scalars().all())

    async def list_agents(self) -> List[Agent]:
        return list((await self._session.execute(
            select(Agent).order_by(Agent.name)
        )).scalars().all())

    async def agent_exists(self, name: str) -> bool:
        return (await self._session.get(Agent, name)) is not None

    async def agent_units_for_unit(self, unit_name: str) -> List[AgentUnit]:
        return list((await self._session.execute(
            select(AgentUnit).where(AgentUnit.unit_name == unit_name)
            .order_by(AgentUnit.agent_name)
        )).scalars().all())

    async def get_agent_unit(self, agent_name: str, unit_name: str) -> Optional[AgentUnit]:
        return await self._session.get(AgentUnit, (agent_name, unit_name))

    # ---- 写(staging,不 commit)-------------------------------------------

    def add_unit(self, unit: ToolUnit, members: List[ToolMember]) -> None:
        self._session.add(unit)
        for m in members:
            self._session.add(m)

    async def replace_members(self, unit_name: str, members: List[ToolMember]) -> None:
        await self._session.execute(
            delete(ToolMember).where(ToolMember.unit_name == unit_name)
        )
        for m in members:
            self._session.add(m)

    async def delete_unit(self, name: str) -> None:
        """显式删全部子行(dialect-safe,不赖 per-connection FK pragma);DB FK 是双保险。
        含 ToolCredential —— 让本方法自洽,不依赖调用方记得另调 creds.delete_for_unit
        (否则直接调用者会在 FK 关闭的 SQLite 上孤儿化凭证,reviewer #7)。"""
        await self._session.execute(delete(AgentUnit).where(AgentUnit.unit_name == name))
        await self._session.execute(delete(ToolCredential).where(ToolCredential.unit_name == name))
        await self._session.execute(delete(ToolMember).where(ToolMember.unit_name == name))
        await self._session.execute(delete(ToolUnit).where(ToolUnit.name == name))

    def add_agent_unit(self, agent_unit: AgentUnit) -> None:
        self._session.add(agent_unit)

    async def delete_agent_unit(self, agent_name: str, unit_name: str) -> None:
        await self._session.execute(
            delete(AgentUnit).where(
                AgentUnit.agent_name == agent_name,
                AgentUnit.unit_name == unit_name,
            )
        )

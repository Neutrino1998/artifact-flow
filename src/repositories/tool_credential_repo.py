"""
ToolCredential Repository —— external 工具 unit 级加密凭证的纯数据访问(B-4)。

只碰行,不解密(密文进出由上层 cipher 处理)。ORM 实例不外逃语义同其它 repo:
调用方拿到的是行对象,只在 session 内读其列(placeholder_name/encrypted_value/source)。
"""

from typing import Dict, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ToolCredential
from repositories.base import BaseRepository


class ToolCredentialRepository(BaseRepository[ToolCredential]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, ToolCredential)

    async def list_for_unit(self, unit_name: str) -> List[ToolCredential]:
        """某 unit 的全部凭证行(含密文)。resolver 在 execute 期按 unit 名直查(B-5 lazy)。"""
        return list((await self._session.execute(
            select(ToolCredential)
            .where(ToolCredential.unit_name == unit_name)
            .order_by(ToolCredential.placeholder_name)
        )).scalars().all())

    async def placeholder_map(self, unit_name: str) -> Dict[str, str]:
        """{placeholder_name: source} —— 不含密文,供写-only CRUD 的掩码 GET 列举。"""
        rows = (await self._session.execute(
            select(ToolCredential.placeholder_name, ToolCredential.source)
            .where(ToolCredential.unit_name == unit_name)
            .order_by(ToolCredential.placeholder_name)
        )).all()
        return {name: source for name, source in rows}

    async def upsert(
        self, unit_name: str, placeholder_name: str, encrypted_value: str, source: str
    ) -> None:
        """写一行密文。已存在则覆盖密文/source(让 onupdate 触发)。"""
        row = await self._session.get(ToolCredential, (unit_name, placeholder_name))
        if row is None:
            self._session.add(ToolCredential(
                unit_name=unit_name,
                placeholder_name=placeholder_name,
                encrypted_value=encrypted_value,
                source=source,
            ))
        else:
            row.encrypted_value = encrypted_value
            row.source = source

    async def delete_placeholder(self, unit_name: str, placeholder_name: str) -> bool:
        """删一行;返回是否真删到(供上层把 no-op 区分成 404)。"""
        result = await self._session.execute(
            delete(ToolCredential).where(
                ToolCredential.unit_name == unit_name,
                ToolCredential.placeholder_name == placeholder_name,
            )
        )
        return result.rowcount > 0

    async def prune_unreferenced(self, unit_name: str, referenced: set) -> None:
        """删该 unit 内占位符 ∉ referenced 的凭证行(update_unit 后,定义不再引用的 dynamic
        凭证对称清理)。referenced 空 → 删该 unit 全部凭证。"""
        stmt = delete(ToolCredential).where(ToolCredential.unit_name == unit_name)
        if referenced:
            stmt = stmt.where(ToolCredential.placeholder_name.notin_(referenced))
        await self._session.execute(stmt)

    async def delete_for_unit(self, unit_name: str) -> None:
        await self._session.execute(
            delete(ToolCredential).where(ToolCredential.unit_name == unit_name)
        )

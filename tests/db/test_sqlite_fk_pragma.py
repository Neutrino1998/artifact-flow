"""SQLite per-connection FK pragma 回归(E-2 reviewer #1)。

`PRAGMA foreign_keys` 是 per-connection 的:老实现只在 init 连接上设,文件库多连接
池的其余连接 FK=OFF → DB 级 ondelete=CASCADE 静默不生效(Core DELETE 留孤儿
user_skill 行)。修复 = database.py 的 connect 事件监听器,每条新连接强制开启。

本测试用文件库 + `engine.dispose()` 逼池子发**全新连接**(init 连接连同其 pragma
一起丢弃)—— 在旧实现下必然复现孤儿行;:memory: StaticPool 单连接结构性抓不到。
"""

import uuid

import pytest
from sqlalchemy import delete, select

from api.services.auth import hash_password
from db.database import DatabaseManager
from db.models import Skill, User, UserSkill


@pytest.fixture
async def file_db(tmp_path):
    manager = DatabaseManager(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fk.db'}"
    )
    await manager.initialize()
    yield manager
    await manager.close()


async def test_core_delete_cascades_on_fresh_pool_connection(file_db):
    # 丢弃 init 期的连接(老实现里唯一带 FK pragma 的那条),后续 session 全部
    # 走池子新发的连接 —— 没有 per-connection 监听器时它们 FK=OFF。
    await file_db._engine.dispose()

    user_id = str(uuid.uuid4())
    async with file_db.session() as session:
        session.add(User(
            id=user_id, username=f"fk-{user_id[:8]}",
            hashed_password=hash_password("x-pass-123"), role="user", is_active=True,
        ))
        await session.flush()  # User 先落,Skill.owner FK 才有目标(无 ORM 关系,UoW 不排序)
        session.add(Skill(
            slug="fk-probe", name="fk-probe", description="d", visibility="private",
            default_enabled=True, source="dynamic", owner_user_id=user_id,
            skill_md="body",
        ))
        await session.flush()
        session.add(UserSkill(user_id=user_id, skill_slug="fk-probe", enabled=False))
        await session.commit()

    # SkillRepository.delete_skill 同款 Core DELETE:级联清理完全依赖 DB 级
    # ondelete=CASCADE(Skill 与 UserSkill 之间无 ORM relationship)
    async with file_db.session() as session:
        await session.execute(delete(Skill).where(Skill.slug == "fk-probe"))
        await session.commit()

    async with file_db.session() as session:
        orphan = (await session.execute(
            select(UserSkill).where(UserSkill.skill_slug == "fk-probe")
        )).scalar_one_or_none()
        assert orphan is None, "user_skill 孤儿行:FK pragma 未在该连接上生效"

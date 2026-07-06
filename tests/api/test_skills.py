"""skill 管理 REST:C-3 列举/toggle + E-2 导入/导出/删除(user/admin 双通道)。"""

import io
import uuid
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from api.services.auth import hash_password
from config import config
from db.models import Skill, User, UserSkill


async def _seed_skill(db_session, slug, visibility="public", default_enabled=True,
                      source="seeded", owner_user_id=None, bundle=None):
    db_session.add(Skill(
        slug=slug, name=slug.title(), description="d", visibility=visibility,
        default_enabled=default_enabled, source=source, skill_md="body",
        owner_user_id=owner_user_id, bundle=bundle,
    ))
    await db_session.commit()


GOOD_MD = """---
name: my-skill
description: does useful things
---
# My Skill

Follow the steps.
"""


def _zip(md: str = GOOD_MD, extra: dict = None, wrapper: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        prefix = f"{wrapper}/" if wrapper else ""
        zf.writestr(f"{prefix}SKILL.md", md)
        for name, data in (extra or {}).items():
            zf.writestr(f"{prefix}{name}", data)
    return buf.getvalue()


def _upload(blob: bytes, filename: str = "my-skill.zip") -> dict:
    return {"file": (filename, blob, "application/zip")}


class TestListSkills:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        assert (await anon_client.get("/api/v1/skills")).status_code == 401

    async def test_lists_visible_with_effective_state(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "pub", default_enabled=True)
        await _seed_skill(db_session, "off", default_enabled=False)
        await _seed_skill(db_session, "priv", visibility="private")  # 非 owner → 不可见

        r = await client.get("/api/v1/skills")
        assert r.status_code == 200
        items = {s["slug"]: s for s in r.json()["skills"]}
        assert set(items) == {"pub", "off"}          # private 不列
        assert items["pub"]["enabled"] is True
        assert items["off"]["enabled"] is False
        assert items["pub"]["is_overridden"] is False


class TestToggleSkill:
    async def test_toggle_persists(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "pub", default_enabled=True)

        r = await client.put("/api/v1/skills/pub/enabled", json={"enabled": False})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False and body["is_overridden"] is True

        # 重列反映覆盖态
        items = {s["slug"]: s for s in (await client.get("/api/v1/skills")).json()["skills"]}
        assert items["pub"]["enabled"] is False
        assert items["pub"]["default_enabled"] is True

    async def test_toggle_invisible_404(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "priv", visibility="private")  # owner 非当前用户
        r = await client.put("/api/v1/skills/priv/enabled", json={"enabled": True})
        assert r.status_code == 404

    async def test_toggle_unknown_404(self, client: AsyncClient):
        r = await client.put("/api/v1/skills/ghost/enabled", json={"enabled": True})
        assert r.status_code == 404

    async def test_anon_blocked(self, anon_client: AsyncClient):
        r = await anon_client.put("/api/v1/skills/pub/enabled", json={"enabled": True})
        assert r.status_code == 401


async def _add_user(db_session, username: str) -> User:
    user = User(
        id=str(uuid.uuid4()), username=username,
        hashed_password=hash_password("x-pass-123"), role="user", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


class TestImportSkill:
    async def test_import_private_happy_path(self, client: AsyncClient, db_session):
        blob = _zip()
        r = await client.post("/api/v1/skills/import", files=_upload(blob))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "imported"
        sk = body["skill"]
        assert sk["slug"] == "my-skill"
        assert sk["visibility"] == "private" and sk["is_owner"] is True
        assert sk["source"] == "dynamic" and sk["has_bundle"] is True
        assert sk["enabled"] is True and sk["default_enabled"] is True  # 私有即刻进 L1

        # bundle = 原始字节(导出无损前提);行字段镜像通道语义
        row = (await db_session.execute(
            select(Skill).where(Skill.slug == "my-skill")
        )).scalar_one()
        assert row.bundle == blob
        assert row.source == "dynamic" and row.seed_hash is None
        assert row.visibility == "private" and row.owner_user_id is not None
        assert row.skill_md.startswith("# My Skill")  # frontmatter 已剥,正文未改写

        # 列表可见
        items = {s["slug"] for s in (await client.get("/api/v1/skills")).json()["skills"]}
        assert "my-skill" in items

    async def test_import_bad_zip_422_structured_findings(self, client: AsyncClient):
        r = await client.post("/api/v1/skills/import", files=_upload(b"not a zip"))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "findings" in detail
        assert any(f["rule"] == "zip.invalid" for f in detail["findings"])

    async def test_import_empty_body_422(self, client: AsyncClient):
        md = "---\nname: empty-skill\ndescription: d\n---\n\n"
        r = await client.post("/api/v1/skills/import", files=_upload(_zip(md)))
        assert r.status_code == 422
        assert any(
            f["rule"] == "md.body_empty" for f in r.json()["detail"]["findings"]
        )

    async def test_import_warnings_surfaced_but_imported(self, client: AsyncClient):
        md = GOOD_MD + "\nSee [helper](scripts/run.py).\n"
        r = await client.post("/api/v1/skills/import", files=_upload(_zip(md)))
        assert r.status_code == 200
        rules = {f["rule"] for f in r.json()["findings"]}
        assert "md.link_unresolved" in rules

    async def test_import_fm_visibility_ignored_warning(self, client: AsyncClient):
        md = "---\nname: vis-skill\ndescription: d\nvisibility: public\n---\nbody\n"
        r = await client.post("/api/v1/skills/import", files=_upload(_zip(md)))
        assert r.status_code == 200
        assert r.json()["skill"]["visibility"] == "private"  # 通道决定,声明被忽略
        assert any(
            f["rule"] == "fm.import_ignored_keys" for f in r.json()["findings"]
        )

    async def test_import_unknown_allowed_tools_warns(self, client: AsyncClient):
        md = "---\nname: t-skill\ndescription: d\nallowed-tools: no_such_unit\n---\nbody\n"
        r = await client.post("/api/v1/skills/import", files=_upload(_zip(md)))
        assert r.status_code == 200
        assert any(f["rule"] == "tools.unknown_entry" for f in r.json()["findings"])

    async def test_import_collision_with_seeded_409(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "my-skill")
        r = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 409

    async def test_import_collision_neutral_message(self, client: AsyncClient, db_session):
        # 撞他人 private:同中性文案,不泄露归属/可见性
        other = await _add_user(db_session, "other-owner")
        await _seed_skill(db_session, "my-skill", visibility="private",
                          source="dynamic", owner_user_id=other.id)
        r = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 409
        assert "private" not in r.json()["detail"]
        assert other.username not in r.json()["detail"]

    async def test_import_over_bundle_cap_422(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(config, "SKILL_BUNDLE_MAX_BYTES", 10)
        r = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 422
        assert any(
            f["rule"] == "zip.bundle_too_large" for f in r.json()["detail"]["findings"]
        )

    async def test_import_over_quota_413(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 10)
        r = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 413

    async def test_bundle_bytes_count_into_storage(self, client: AsyncClient):
        blob = _zip()
        assert (await client.post(
            "/api/v1/skills/import", files=_upload(blob)
        )).status_code == 200
        usage = (await client.get("/api/v1/chat/storage")).json()
        assert usage["used_bytes"] >= len(blob)  # skill bundle 计入共用池

    async def test_anon_blocked(self, anon_client: AsyncClient):
        r = await anon_client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 401


class TestAdminImportSkill:
    async def test_admin_import_is_shared(
        self, admin_client: AsyncClient, client: AsyncClient
    ):
        r = await admin_client.post("/api/v1/admin/skills/import", files=_upload(_zip()))
        assert r.status_code == 200
        sk = r.json()["skill"]
        assert sk["visibility"] == "public"
        assert sk["default_enabled"] is True and sk["enabled"] is True
        assert sk["is_owner"] is False  # owner=null(marketplace 语义)

        # 第二用户(普通 client)可见,默认进 L1,仍可个人关闭
        items = {s["slug"]: s for s in (await client.get("/api/v1/skills")).json()["skills"]}
        assert "my-skill" in items
        assert items["my-skill"]["enabled"] is True
        closed = await client.put("/api/v1/skills/my-skill/enabled", json={"enabled": False})
        assert closed.status_code == 200
        assert closed.json()["enabled"] is False
        after_close = {
            s["slug"]: s for s in (await client.get("/api/v1/skills")).json()["skills"]
        }
        assert after_close["my-skill"]["enabled"] is False
        assert after_close["my-skill"]["is_overridden"] is True

    async def test_admin_import_quota_exempt(self, admin_client: AsyncClient, monkeypatch):
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 10)
        r = await admin_client.post("/api/v1/admin/skills/import", files=_upload(_zip()))
        assert r.status_code == 200

    async def test_non_admin_403(self, client: AsyncClient):
        r = await client.post("/api/v1/admin/skills/import", files=_upload(_zip()))
        assert r.status_code == 403


class TestExportSkill:
    async def test_export_roundtrip_bytes_equal(self, client: AsyncClient):
        blob = _zip(GOOD_MD, extra={"scripts/run.py": "print(1)\n"})
        assert (await client.post(
            "/api/v1/skills/import", files=_upload(blob)
        )).status_code == 200
        r = await client.get("/api/v1/skills/my-skill/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert r.content == blob  # 无损:原始字节原样返还

    async def test_export_prose_skill_400(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "prose")  # bundle NULL
        r = await client.get("/api/v1/skills/prose/export")
        assert r.status_code == 400

    async def test_export_invisible_404(self, client: AsyncClient, db_session):
        other = await _add_user(db_session, "exp-owner")
        await _seed_skill(db_session, "theirs", visibility="private",
                          source="dynamic", owner_user_id=other.id, bundle=_zip())
        assert (await client.get("/api/v1/skills/theirs/export")).status_code == 404
        assert (await client.get("/api/v1/skills/ghost/export")).status_code == 404


class TestDeleteSkill:
    async def test_delete_own_dynamic_cascades(self, client: AsyncClient, db_session):
        assert (await client.post(
            "/api/v1/skills/import", files=_upload(_zip())
        )).status_code == 200
        # 造一行 user_skill 覆盖,验证 FK CASCADE 清理
        assert (await client.put(
            "/api/v1/skills/my-skill/enabled", json={"enabled": False}
        )).status_code == 200

        assert (await client.delete("/api/v1/skills/my-skill")).status_code == 204

        assert (await db_session.execute(
            select(Skill).where(Skill.slug == "my-skill")
        )).scalar_one_or_none() is None
        assert (await db_session.execute(
            select(UserSkill).where(UserSkill.skill_slug == "my-skill")
        )).scalar_one_or_none() is None
        items = {s["slug"] for s in (await client.get("/api/v1/skills")).json()["skills"]}
        assert "my-skill" not in items

    async def test_delete_seeded_400(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "pub")
        assert (await client.delete("/api/v1/skills/pub")).status_code == 400

    async def test_delete_shared_non_owner_403(
        self, admin_client: AsyncClient, client: AsyncClient
    ):
        assert (await admin_client.post(
            "/api/v1/admin/skills/import", files=_upload(_zip())
        )).status_code == 200
        assert (await client.delete("/api/v1/skills/my-skill")).status_code == 403

    async def test_delete_invisible_404(self, client: AsyncClient, db_session):
        other = await _add_user(db_session, "del-owner")
        await _seed_skill(db_session, "theirs", visibility="private",
                          source="dynamic", owner_user_id=other.id)
        assert (await client.delete("/api/v1/skills/theirs")).status_code == 404
        assert (await client.delete("/api/v1/skills/ghost")).status_code == 404

    async def test_admin_delete_any_dynamic(
        self, admin_client: AsyncClient, client: AsyncClient, db_session
    ):
        # 用户私有 skill,admin 通道可删(绕过可见性)
        assert (await client.post(
            "/api/v1/skills/import", files=_upload(_zip())
        )).status_code == 200
        assert (await admin_client.delete(
            "/api/v1/admin/skills/my-skill"
        )).status_code == 204
        assert (await db_session.execute(
            select(Skill).where(Skill.slug == "my-skill")
        )).scalar_one_or_none() is None

    async def test_admin_delete_seeded_400(self, admin_client: AsyncClient, db_session):
        await _seed_skill(db_session, "pub")
        assert (await admin_client.delete("/api/v1/admin/skills/pub")).status_code == 400

    async def test_admin_endpoint_non_admin_403(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "x", source="dynamic")
        assert (await client.delete("/api/v1/admin/skills/x")).status_code == 403

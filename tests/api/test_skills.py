"""skill 管理 REST:C-3 列举/toggle + E-2 导入/导出/删除(user/admin 双通道)。"""

import io
import uuid
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from api.services.auth import hash_password
from config import config
from core.skill_manager import SkillManager
from db.models import Department, DepartmentSkillRule, Skill, User, UserSkill
from utils.skill_validator import validate_skill_zip


async def _seed_skill(db_session, slug, visibility="public", default_enabled=True,
                      source="seeded", owner_user_id=None, bundle=None,
                      has_extra_files=False):
    if bundle is None:
        bundle = _zip(
            f"---\nname: {slug.title()}\ndescription: d\n---\nbody\n"
        )
    row = Skill(
        slug=slug, name=slug.title(), description="d", visibility=visibility,
        default_enabled=default_enabled, source=source, skill_md="body",
        owner_user_id=owner_user_id, namespace_key=owner_user_id or "",
        bundle=bundle, has_extra_files=has_extra_files,
    )
    db_session.add(row)
    await db_session.commit()
    return row


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


def _named_upload(slug: str) -> dict:
    md = f"---\nname: {slug}\ndescription: d\n---\n# {slug}\n"
    return _upload(_zip(md), filename=f"{slug}.zip")


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


class TestSkillDetail:
    async def test_reads_visible_guidance_body(
        self, client: AsyncClient, db_session
    ):
        row = await _seed_skill(db_session, "guide", has_extra_files=True)
        row.skill_md = "# Guide\n\nFollow the steps."
        await db_session.commit()

        r = await client.get(f"/api/v1/skills/{row.id}")

        assert r.status_code == 200, r.text
        assert r.json() == {
            "id": row.id,
            "slug": "guide",
            "name": "Guide",
            "description": "d",
            "skill_md": "# Guide\n\nFollow the steps.",
            "source": "seeded",
            "visibility": "public",
            "has_extra_files": True,
        }

    async def test_disabled_visible_skill_remains_readable(
        self, client: AsyncClient, db_session
    ):
        row = await _seed_skill(db_session, "off", default_enabled=False)

        r = await client.get(f"/api/v1/skills/{row.id}")

        assert r.status_code == 200
        assert r.json()["skill_md"] == "body"

    async def test_invisible_and_unknown_return_404(
        self, client: AsyncClient, db_session
    ):
        other = await _add_user(db_session, "detail-owner")
        private = await _seed_skill(
            db_session,
            "theirs",
            visibility="private",
            source="dynamic",
            owner_user_id=other.id,
        )

        assert (await client.get(f"/api/v1/skills/{private.id}")).status_code == 404
        assert (await client.get("/api/v1/skills/ghost")).status_code == 404

    async def test_anon_blocked(self, anon_client: AsyncClient):
        assert (await anon_client.get("/api/v1/skills/ghost")).status_code == 401


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
        assert sk["source"] == "dynamic" and sk["has_extra_files"] is False
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

    async def test_private_import_can_shadow_same_slug_shared(
        self, client: AsyncClient, db_session
    ):
        await _seed_skill(db_session, "my-skill")
        r = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 200
        items = (await client.get("/api/v1/skills")).json()["skills"]
        same_slug = [item for item in items if item["slug"] == "my-skill"]
        assert len(same_slug) == 2
        shared = next(item for item in same_slug if item["visibility"] == "public")
        private = next(item for item in same_slug if item["visibility"] == "private")
        assert shared["shadowed_by_private"] is True
        assert shared["enabled"] is False
        assert private["shadowed_by_private"] is False
        assert private["enabled"] is True
        toggle = await client.put(
            f"/api/v1/skills/{shared['id']}/enabled", json={"enabled": True}
        )
        assert toggle.status_code == 409
        assert "同名私人技能覆盖" in toggle.json()["detail"]

    async def test_other_users_can_import_same_private_slug(
        self, client: AsyncClient, db_session
    ):
        other = await _add_user(db_session, "other-owner")
        await _seed_skill(db_session, "my-skill", visibility="private",
                          source="dynamic", owner_user_id=other.id)
        r = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 200
        rows = (await db_session.execute(
            select(Skill).where(Skill.slug == "my-skill")
        )).scalars().all()
        assert len(rows) == 2
        owners = {row.owner_user_id for row in rows}
        assert other.id in owners
        assert None not in owners

    async def test_same_user_private_slug_still_conflicts(self, client: AsyncClient):
        first = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert first.status_code == 200
        r = await client.post("/api/v1/skills/import", files=_upload(_zip()))
        assert r.status_code == 409

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

    async def test_import_closed_when_private_limit_is_zero(
        self, client: AsyncClient, db_session, monkeypatch
    ):
        monkeypatch.setattr(config, "SKILL_USER_MAX_PRIVATE_COUNT", 0)
        r = await client.post(
            "/api/v1/skills/import", files=_named_upload("closed-skill")
        )
        assert r.status_code == 409
        assert "个人技能导入已关闭" in r.json()["detail"]
        assert (await db_session.execute(
            select(Skill).where(Skill.slug == "closed-skill")
        )).scalar_one_or_none() is None

    async def test_import_rejects_when_private_count_limit_is_reached(
        self, client: AsyncClient, db_session, test_user: User, monkeypatch
    ):
        monkeypatch.setattr(config, "SKILL_USER_MAX_PRIVATE_COUNT", 1)
        first = await client.post(
            "/api/v1/skills/import", files=_named_upload("first-private")
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/v1/skills/import", files=_named_upload("second-private")
        )
        assert second.status_code == 409
        assert "最多可以保留 1 个个人技能" in second.json()["detail"]
        owned = (await db_session.execute(
            select(Skill.slug).where(Skill.owner_user_id == test_user.id)
        )).scalars().all()
        assert owned == ["first-private"]

    async def test_delete_releases_private_skill_slot(
        self, client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(config, "SKILL_USER_MAX_PRIVATE_COUNT", 1)
        assert (await client.post(
            "/api/v1/skills/import", files=_named_upload("old-private")
        )).status_code == 200
        assert (await client.delete("/api/v1/skills/old-private")).status_code == 204
        assert (await client.post(
            "/api/v1/skills/import", files=_named_upload("new-private")
        )).status_code == 200

    async def test_shared_skills_do_not_consume_private_limit(
        self, client: AsyncClient, db_session, monkeypatch
    ):
        monkeypatch.setattr(config, "SKILL_USER_MAX_PRIVATE_COUNT", 1)
        await _seed_skill(db_session, "shared-a")
        await _seed_skill(db_session, "shared-b", source="dynamic")
        r = await client.post(
            "/api/v1/skills/import", files=_named_upload("one-private")
        )
        assert r.status_code == 200

    async def test_minus_one_allows_unlimited_private_imports(
        self, client: AsyncClient, db_session, test_user: User, monkeypatch
    ):
        monkeypatch.setattr(config, "SKILL_USER_MAX_PRIVATE_COUNT", -1)
        for slug in ("private-a", "private-b", "private-c", "private-d"):
            r = await client.post(
                "/api/v1/skills/import", files=_named_upload(slug)
            )
            assert r.status_code == 200, r.text
        owned = (await db_session.execute(
            select(Skill.slug).where(Skill.owner_user_id == test_user.id)
        )).scalars().all()
        assert set(owned) == {"private-a", "private-b", "private-c", "private-d"}

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

    async def test_admin_import_shared_department_default_off(
        self, admin_client: AsyncClient, db_session
    ):
        r = await admin_client.post(
            "/api/v1/admin/skills/import",
            data={"visibility": "department", "default_enabled": "false"},
            files=_upload(_zip()),
        )

        assert r.status_code == 200, r.text
        sk = r.json()["skill"]
        assert sk["visibility"] == "department"
        assert sk["default_enabled"] is False
        assert sk["enabled"] is False
        row = (await db_session.execute(
            select(Skill).where(Skill.slug == "my-skill")
        )).scalar_one()
        assert row.visibility == "department"
        assert row.default_enabled is False

    async def test_admin_import_quota_exempt(self, admin_client: AsyncClient, monkeypatch):
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 10)
        r = await admin_client.post("/api/v1/admin/skills/import", files=_upload(_zip()))
        assert r.status_code == 200

    async def test_admin_shared_import_exempt_when_private_imports_closed(
        self, admin_client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(config, "SKILL_USER_MAX_PRIVATE_COUNT", 0)
        r = await admin_client.post(
            "/api/v1/admin/skills/import", files=_named_upload("shared-by-admin")
        )
        assert r.status_code == 200
        assert r.json()["skill"]["is_owner"] is False

    async def test_non_admin_403(self, client: AsyncClient):
        r = await client.post("/api/v1/admin/skills/import", files=_upload(_zip()))
        assert r.status_code == 403


class TestAdminSharedSkillManagement:
    async def test_list_shared_skills_only(
        self, admin_client: AsyncClient, db_session, test_user: User
    ):
        await _seed_skill(db_session, "seeded")
        await _seed_skill(db_session, "dynamic", source="dynamic")
        await _seed_skill(
            db_session,
            "dept-only",
            visibility="department",
            source="dynamic",
            default_enabled=False,
        )
        await _seed_skill(
            db_session,
            "private",
            visibility="private",
            source="dynamic",
            owner_user_id=test_user.id,
        )

        r = await admin_client.get("/api/v1/admin/skills")

        assert r.status_code == 200
        items = {s["slug"]: s for s in r.json()["skills"]}
        assert set(items) == {"dept-only", "dynamic", "seeded"}
        assert items["seeded"]["can_edit"] is False
        assert items["dynamic"]["can_edit"] is True
        assert items["dept-only"]["visibility"] == "department"
        assert items["dept-only"]["default_enabled"] is False

    async def test_detail_bypasses_admin_department_scope(
        self, admin_client: AsyncClient, db_session
    ):
        row = await _seed_skill(
            db_session,
            "dept-only",
            visibility="department",
            source="dynamic",
        )
        row.skill_md = "# Department guidance"
        await db_session.commit()

        assert (await admin_client.get(f"/api/v1/skills/{row.id}")).status_code == 404
        r = await admin_client.get(f"/api/v1/admin/skills/{row.id}")

        assert r.status_code == 200, r.text
        assert r.json()["skill_md"] == "# Department guidance"
        assert r.json()["visibility"] == "department"

    async def test_detail_excludes_private_skills_and_non_admins(
        self,
        admin_client: AsyncClient,
        client: AsyncClient,
        db_session,
        test_user: User,
    ):
        private = await _seed_skill(
            db_session,
            "private-detail",
            visibility="private",
            source="dynamic",
            owner_user_id=test_user.id,
        )

        assert (
            await admin_client.get(f"/api/v1/admin/skills/{private.id}")
        ).status_code == 404
        assert (
            await client.get("/api/v1/admin/skills/ghost")
        ).status_code == 403

    async def test_patch_dynamic_shared_updates_and_clears_dept_rules(
        self, admin_client: AsyncClient, db_session, test_user: User
    ):
        shared = await _seed_skill(db_session, "shared", source="dynamic")
        db_session.add(Department(id="dept-a", name="Dept A"))
        db_session.add(DepartmentSkillRule(department_id="dept-a", skill_id=shared.id))
        db_session.add(UserSkill(
            user_id=test_user.id, skill_id=shared.id, enabled=False
        ))
        await db_session.commit()

        r = await admin_client.patch(
            "/api/v1/admin/skills/shared",
            json={"visibility": "department", "default_enabled": False},
        )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["visibility"] == "department"
        assert body["default_enabled"] is False
        await db_session.refresh(shared)
        row = (await db_session.execute(
            select(Skill).where(Skill.slug == "shared")
        )).scalar_one()
        assert row.visibility == "department"
        assert row.default_enabled is False
        assert (await db_session.execute(
            select(DepartmentSkillRule).where(
                DepartmentSkillRule.skill_id == shared.id
            )
        )).scalar_one_or_none() is None
        assert (await db_session.execute(
            select(UserSkill).where(UserSkill.skill_id == shared.id)
        )).scalar_one_or_none() is not None

    async def test_patch_default_enabled_only_keeps_dept_rules(
        self, admin_client: AsyncClient, db_session
    ):
        shared = await _seed_skill(
            db_session, "shared", visibility="department", source="dynamic"
        )
        db_session.add(Department(id="dept-a", name="Dept A"))
        db_session.add(DepartmentSkillRule(department_id="dept-a", skill_id=shared.id))
        await db_session.commit()

        r = await admin_client.patch(
            "/api/v1/admin/skills/shared", json={"default_enabled": False}
        )

        assert r.status_code == 200, r.text
        assert r.json()["default_enabled"] is False
        assert (await db_session.execute(
            select(DepartmentSkillRule).where(
                DepartmentSkillRule.department_id == "dept-a",
                DepartmentSkillRule.skill_id == shared.id,
            )
        )).scalar_one_or_none() is not None

    async def test_patch_seeded_400(self, admin_client: AsyncClient, db_session):
        await _seed_skill(db_session, "seeded")

        r = await admin_client.patch(
            "/api/v1/admin/skills/seeded", json={"default_enabled": False}
        )

        assert r.status_code == 400
        assert "config" in r.json()["detail"]

    async def test_patch_private_dynamic_400(
        self, admin_client: AsyncClient, db_session, test_user: User
    ):
        private = await _seed_skill(
            db_session,
            "private",
            visibility="private",
            source="dynamic",
            owner_user_id=test_user.id,
        )

        r = await admin_client.patch(
            f"/api/v1/admin/skills/{private.id}", json={"visibility": "public"}
        )

        assert r.status_code == 400
        assert "user-owned" in r.json()["detail"]

    async def test_patch_missing_404(self, admin_client: AsyncClient):
        r = await admin_client.patch(
            "/api/v1/admin/skills/ghost", json={"default_enabled": False}
        )

        assert r.status_code == 404

    async def test_non_admin_blocked(self, client: AsyncClient):
        assert (await client.get("/api/v1/admin/skills")).status_code == 403
        r = await client.patch(
            "/api/v1/admin/skills/x", json={"default_enabled": False}
        )
        assert r.status_code == 403

    async def test_update_dynamic_shared_uses_locked_skill_read(
        self, db_session, monkeypatch
    ):
        await _seed_skill(db_session, "shared", source="dynamic")
        mgr = SkillManager(db_session)
        original = mgr._repo.get_shared_skill
        called = False

        async def tracked(identifier: str, *, for_update: bool = False):
            nonlocal called
            called = True
            assert for_update is True
            return await original(identifier, for_update=for_update)

        monkeypatch.setattr(mgr._repo, "get_shared_skill", tracked)

        await mgr.update_admin_shared(
            "admin-user", "shared", default_enabled=False
        )

        assert called is True


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

    async def test_export_single_file_skill_returns_stored_zip(
        self, client: AsyncClient, db_session
    ):
        await _seed_skill(db_session, "prose")
        r = await client.get("/api/v1/skills/prose/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        result = validate_skill_zip(r.content, where="prose.zip")
        assert result.ok, [f"{f.rule}: {f.message}" for f in result.errors]
        assert result.parsed is not None
        assert result.parsed.names == ["SKILL.md"]
        assert result.parsed.frontmatter["name"] == "Prose"
        assert result.parsed.frontmatter["description"] == "d"
        assert result.parsed.body == "body"

    async def test_same_slug_candidates_export_exact_bundle_by_id(
        self, client: AsyncClient, db_session
    ):
        shared_blob = _zip(
            "---\nname: my-skill\ndescription: shared\n---\nshared body\n"
        )
        private_blob = _zip(
            "---\nname: my-skill\ndescription: private\n---\nprivate body\n"
        )
        await _seed_skill(db_session, "my-skill", bundle=shared_blob)
        assert (await client.post(
            "/api/v1/skills/import", files=_upload(private_blob)
        )).status_code == 200

        items = (await client.get("/api/v1/skills")).json()["skills"]
        same_slug = [item for item in items if item["slug"] == "my-skill"]
        shared = next(item for item in same_slug if not item["is_owner"])
        private = next(item for item in same_slug if item["is_owner"])

        shared_export = await client.get(
            f"/api/v1/skills/{shared['id']}/export"
        )
        private_export = await client.get(
            f"/api/v1/skills/{private['id']}/export"
        )
        assert shared_export.content == shared_blob
        assert private_export.content == private_blob
        assert shared_export.headers["content-disposition"].endswith(
            'filename="my-skill.zip"'
        )

    async def test_export_invisible_404(self, client: AsyncClient, db_session):
        other = await _add_user(db_session, "exp-owner")
        await _seed_skill(db_session, "theirs", visibility="private",
                          source="dynamic", owner_user_id=other.id, bundle=_zip())
        assert (await client.get("/api/v1/skills/theirs/export")).status_code == 404
        assert (await client.get("/api/v1/skills/ghost/export")).status_code == 404

    async def test_admin_export_shared_department_bypasses_admin_visibility(
        self, admin_client: AsyncClient, db_session
    ):
        blob = _zip(
            "---\nname: dept-skill\ndescription: d\n---\nbody\n"
        )
        await _seed_skill(
            db_session,
            "dept-skill",
            visibility="department",
            source="dynamic",
            bundle=blob,
        )

        assert (
            await admin_client.get("/api/v1/skills/dept-skill/export")
        ).status_code == 404
        r = await admin_client.get("/api/v1/admin/skills/dept-skill/export")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert r.content == blob

    async def test_admin_export_user_private_stays_out_of_shared_catalog(
        self, admin_client: AsyncClient, db_session
    ):
        owner = await _add_user(db_session, "private-export-owner")
        await _seed_skill(
            db_session,
            "private-skill",
            visibility="private",
            source="dynamic",
            owner_user_id=owner.id,
        )

        r = await admin_client.get("/api/v1/admin/skills/private-skill/export")
        assert r.status_code == 404


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
            select(UserSkill).join(Skill, UserSkill.skill_id == Skill.id).where(
                Skill.slug == "my-skill"
            )
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
        imported = await client.post(
            "/api/v1/skills/import", files=_upload(_zip())
        )
        assert imported.status_code == 200
        assert (await admin_client.delete(
            f"/api/v1/admin/skills/{imported.json()['skill']['id']}"
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

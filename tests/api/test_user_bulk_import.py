"""
PR3 — Bulk-import users endpoint integration tests.

Covers POST /api/v1/admin/users/bulk-import:
- Auth (anon 401, regular user 403)
- Happy path: rows split into created / failed / skipped
- Department auto-creation via resolve_department_path
- File-internal duplicate → 400
- Default password = username (login round-trip)
- Username < 4 chars + empty password → failed (default too short)
- Department gap → failed
- Row count over limit → 400
- Byte size over limit → 422
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from db.models import Department, User


def _csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def _post_csv(client: AsyncClient, csv_bytes: bytes, filename: str = "users.csv"):
    return client.post(
        "/api/v1/admin/users/bulk-import",
        files={"file": (filename, csv_bytes, "text/csv")},
    )


# ============================================================
# Auth
# ============================================================


class TestAuth:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        resp = await _post_csv(anon_client, _csv_bytes("username\nalice\n"))
        assert resp.status_code == 401

    async def test_regular_user_blocked(self, client: AsyncClient):
        resp = await _post_csv(client, _csv_bytes("username\nalice\n"))
        assert resp.status_code == 403


# ============================================================
# Happy path
# ============================================================


class TestHappyPath:
    async def test_minimal_csv_creates_users(
        self, admin_client: AsyncClient, db_manager
    ):
        csv = _csv_bytes("username\nalice\nbobby\ncarol\n")
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_rows"] == 3
        assert len(body["created"]) == 3
        assert body["failed"] == []
        assert body["skipped"] == []
        assert {u["username"] for u in body["created"]} == {"alice", "bobby", "carol"}

        async with db_manager.session() as s:
            result = await s.execute(
                select(User).where(User.username.in_(["alice", "bobby", "carol"]))
            )
            users = result.scalars().all()
            assert len(users) == 3
            for u in users:
                assert u.role == "user"
                assert u.is_active is True
                assert u.department_id is None

    async def test_with_password_and_display_name(
        self, admin_client: AsyncClient, db_manager
    ):
        csv = _csv_bytes(
            "username,password,display_name\n"
            "alice,custompw,Alice Cooper\n"
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 1

        async with db_manager.session() as s:
            user = (await s.execute(
                select(User).where(User.username == "alice")
            )).scalar_one()
            assert user.display_name == "Alice Cooper"

    async def test_default_password_login_roundtrip(
        self, admin_client: AsyncClient, anon_client: AsyncClient
    ):
        """Empty password → default = username; user can log in with username."""
        csv = _csv_bytes("username\nlongenough\n")  # 10 chars > 4
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 1

        login = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "longenough", "password": "longenough"},
        )
        assert login.status_code == 200
        assert "access_token" in login.json()


# ============================================================
# Departments
# ============================================================


class TestDepartments:
    async def test_dept_path_auto_created(
        self, admin_client: AsyncClient, db_manager
    ):
        csv = _csv_bytes(
            "username,dept_l1,dept_l2,dept_l3\n"
            "alice,部门A,子部门A1,小组A1a\n"
            "bobby,部门A,子部门A1,小组A1a\n"  # share leaf with alice
            "carol,部门B,,\n"                  # root-only
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 3

        async with db_manager.session() as s:
            depts = (await s.execute(select(Department))).scalars().all()
            names = {d.name for d in depts}
            # 部门A, 子部门A1, 小组A1a (chain) + 部门B (root) = 4
            assert names == {"部门A", "子部门A1", "小组A1a", "部门B"}

            users = (await s.execute(
                select(User).where(User.username.in_(["alice", "bobby", "carol"]))
            )).scalars().all()
            by_name = {u.username: u for u in users}
            # alice/bobby share the same leaf
            assert by_name["alice"].department_id == by_name["bobby"].department_id
            # carol points to 部门B (root)
            assert by_name["carol"].department_id is not None
            assert by_name["carol"].department_id != by_name["alice"].department_id

    async def test_dept_gap_fails_row(self, admin_client: AsyncClient):
        csv = _csv_bytes(
            "username,dept_l1,dept_l2,dept_l3\n"
            "alice,,,小组A1a\n"     # gap: l1 empty, l3 set
            "bobby,部门A,,小组X\n"  # gap: l2 empty, l3 set
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] == []
        assert len(body["failed"]) == 2
        for f in body["failed"]:
            assert "contiguous" in f["reason"]


# ============================================================
# Validation failures (per-row)
# ============================================================


class TestValidationFailures:
    async def test_invalid_username_fails(self, admin_client: AsyncClient):
        csv = _csv_bytes(
            "username\n"
            "alice\n"          # ok
            "中文用户\n"        # non-ASCII
            "ab cd\n"          # space
            "valid.user_2\n"   # ok
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert {u["username"] for u in body["created"]} == {"alice", "valid.user_2"}
        assert len(body["failed"]) == 2
        failed_usernames = {f["username"] for f in body["failed"]}
        assert "中文用户" in failed_usernames
        assert "ab cd" in failed_usernames

    async def test_short_username_with_empty_password_fails(
        self, admin_client: AsyncClient
    ):
        csv = _csv_bytes("username\nab\n")  # 2 chars, default pwd would also be 2
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] == []
        assert len(body["failed"]) == 1
        assert body["failed"][0]["username"] == "ab"
        assert "password too short" in body["failed"][0]["reason"]

    async def test_short_username_with_explicit_password_ok(
        self, admin_client: AsyncClient
    ):
        csv = _csv_bytes("username,password\nab,longpass\n")
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 1

    async def test_display_name_over_length_fails(
        self, admin_client: AsyncClient
    ):
        """display_name > 128 chars → failed (would otherwise crash on PG/MySQL)."""
        long_name = "x" * 200
        csv = _csv_bytes(
            "username,display_name\n"
            f"alice,{long_name}\n"
            "bobby,正常名字\n"  # control: should still create
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert {u["username"] for u in body["created"]} == {"bobby"}
        assert len(body["failed"]) == 1
        f = body["failed"][0]
        assert f["username"] == "alice"
        assert "display_name too long" in f["reason"]
        assert "200 chars" in f["reason"]

    async def test_dept_name_over_length_fails(
        self, admin_client: AsyncClient
    ):
        """Any dept_l* > 128 chars → failed."""
        long_dept = "y" * 200
        csv = _csv_bytes(
            "username,dept_l1,dept_l2\n"
            f"alice,部门A,{long_dept}\n"
            f"bobby,{long_dept},\n"
            "carol,部门A,子部门A1\n"  # control
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert {u["username"] for u in body["created"]} == {"carol"}
        assert len(body["failed"]) == 2
        reasons = {f["username"]: f["reason"] for f in body["failed"]}
        assert "dept_l2 too long" in reasons["alice"]
        assert "dept_l1 too long" in reasons["bobby"]

    async def test_explicit_password_over_length_fails(
        self, admin_client: AsyncClient
    ):
        """Explicit password > 128 chars → failed (default = username never exceeds)."""
        long_pw = "p" * 200
        csv = _csv_bytes(
            "username,password\n"
            f"alice,{long_pw}\n"
            "bobby,sane_pw\n"  # control
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert {u["username"] for u in body["created"]} == {"bobby"}
        assert len(body["failed"]) == 1
        assert "password too long" in body["failed"][0]["reason"]


# ============================================================
# Skipped (already exists)
# ============================================================


class TestSkipExisting:
    async def test_existing_username_skipped(
        self, admin_client: AsyncClient, test_user: User
    ):
        # test_user has username "testuser"
        csv = _csv_bytes(
            "username\n"
            f"{test_user.username}\n"
            "newuser\n"
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert {u["username"] for u in body["created"]} == {"newuser"}
        assert len(body["skipped"]) == 1
        assert body["skipped"][0]["username"] == test_user.username
        assert body["skipped"][0]["reason"] == "username_exists"


# ============================================================
# File-internal duplicate / parse errors → 400
# ============================================================


class TestFileLevelErrors:
    async def test_internal_duplicate_rejected(self, admin_client: AsyncClient):
        csv = _csv_bytes(
            "username\n"
            "alice\n"
            "bob\n"
            "alice\n"
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 400
        body = resp.json()
        detail = body["detail"]
        assert isinstance(detail, dict)
        assert "duplicate" in detail["message"].lower()
        assert detail["duplicate_rows"] == [{"row": 3, "username": "alice"}]

    async def test_missing_username_column_rejected(self, admin_client: AsyncClient):
        csv = _csv_bytes("password,display_name\nfoo,Bar\n")
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 400
        assert "missing required column" in resp.json()["detail"]

    async def test_row_count_over_limit_rejected(
        self, admin_client: AsyncClient, monkeypatch
    ):
        from config import config as app_config
        monkeypatch.setattr(app_config, "MAX_BULK_IMPORT_ROWS", 5)

        rows = "\n".join(f"user{i:04d}" for i in range(6))
        csv = _csv_bytes(f"username\n{rows}\n")
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 400
        assert "exceeds row limit" in resp.json()["detail"]

    async def test_bytes_over_limit_rejected(
        self, admin_client: AsyncClient, monkeypatch
    ):
        from config import config as app_config
        monkeypatch.setattr(app_config, "MAX_BULK_IMPORT_BYTES", 100)

        csv = _csv_bytes("username\n" + "x" * 200)
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 422
        assert "too large" in resp.json()["detail"].lower()

    async def test_empty_file_rejected(self, admin_client: AsyncClient):
        resp = await _post_csv(admin_client, b"")
        assert resp.status_code == 400


# ============================================================
# Misc
# ============================================================


class TestMisc:
    async def test_unknown_columns_ignored_with_warning(
        self, admin_client: AsyncClient
    ):
        csv = _csv_bytes(
            "username,password,notes,extra\n"
            "alice,longpass,internal note,xyz\n"
        )
        resp = await _post_csv(admin_client, csv)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["created"]) == 1
        assert body["warnings"]
        assert any("Ignored unknown columns" in w for w in body["warnings"])

    async def test_imported_users_default_role_user(
        self, admin_client: AsyncClient, db_manager
    ):
        """role / is_active are hardcoded — CSV cannot promote to admin."""
        csv = _csv_bytes("username\nalice\n")
        await _post_csv(admin_client, csv)
        async with db_manager.session() as s:
            user = (await s.execute(
                select(User).where(User.username == "alice")
            )).scalar_one()
            assert user.role == "user"
            assert user.is_active is True

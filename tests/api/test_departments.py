"""
Departments API integration tests.

Coverage:
- Authentication / authorization (admin-only)
- CRUD: create / list / tree / get / rename / move / delete
- Conflict 409 (root + non-root)
- Cycle detection 400
- Non-empty delete 409 with counts
- Resolve path round-trip
- User integration: create user with department_id
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestDepartmentsAuth:
    """所有端点应当 require_admin。"""

    async def test_anon_blocked(self, anon_client: AsyncClient):
        resp = await anon_client.get("/api/v1/departments")
        assert resp.status_code == 401

    async def test_regular_user_blocked(self, client: AsyncClient):
        resp = await client.get("/api/v1/departments")
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestCreateDepartment:
    async def test_create_root_dept(self, admin_client: AsyncClient):
        resp = await admin_client.post("/api/v1/departments", json={"name": "部门A"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "部门A"
        assert body["parent_id"] is None
        assert body["user_count"] == 0
        assert body["child_count"] == 0
        assert body["id"].startswith("dept-")

    async def test_create_with_parent(self, admin_client: AsyncClient):
        root = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        resp = await admin_client.post(
            "/api/v1/departments",
            json={"name": "子部门A1", "parent_id": root["id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["parent_id"] == root["id"]

    async def test_create_with_invalid_parent(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/departments",
            json={"name": "X", "parent_id": "dept-doesnotexist"},
        )
        assert resp.status_code == 400

    async def test_duplicate_root_name_conflict(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/departments", json={"name": "部门A"})
        resp = await admin_client.post("/api/v1/departments", json={"name": "部门A"})
        assert resp.status_code == 409

    async def test_duplicate_under_same_parent(self, admin_client: AsyncClient):
        root = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        await admin_client.post(
            "/api/v1/departments",
            json={"name": "子部门A1", "parent_id": root["id"]},
        )
        resp = await admin_client.post(
            "/api/v1/departments",
            json={"name": "子部门A1", "parent_id": root["id"]},
        )
        assert resp.status_code == 409

    async def test_same_name_different_parents_ok(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        b = (await admin_client.post("/api/v1/departments", json={"name": "部门B"})).json()
        # Same child name under different parents is allowed
        r1 = await admin_client.post(
            "/api/v1/departments", json={"name": "子部门", "parent_id": a["id"]}
        )
        r2 = await admin_client.post(
            "/api/v1/departments", json={"name": "子部门", "parent_id": b["id"]}
        )
        assert r1.status_code == 200
        assert r2.status_code == 200

    async def test_blank_name_rejected(self, admin_client: AsyncClient):
        resp = await admin_client.post("/api/v1/departments", json={"name": "   "})
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestListAndTree:
    async def test_list_top_level(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/departments", json={"name": "部门A"})
        await admin_client.post("/api/v1/departments", json={"name": "部门B"})
        resp = await admin_client.get("/api/v1/departments")
        assert resp.status_code == 200
        names = sorted(d["name"] for d in resp.json()["departments"])
        assert names == ["部门A", "部门B"]

    async def test_list_children_of_parent(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        await admin_client.post(
            "/api/v1/departments", json={"name": "子部门A1", "parent_id": a["id"]}
        )
        resp = await admin_client.get("/api/v1/departments", params={"parent_id": a["id"]})
        assert resp.status_code == 200
        depts = resp.json()["departments"]
        assert len(depts) == 1
        assert depts[0]["name"] == "子部门A1"

    async def test_tree_structure(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        a1 = (await admin_client.post(
            "/api/v1/departments", json={"name": "子部门A1", "parent_id": a["id"]}
        )).json()
        await admin_client.post(
            "/api/v1/departments", json={"name": "小组A1a", "parent_id": a1["id"]}
        )
        resp = await admin_client.get("/api/v1/departments/tree")
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        assert len(nodes) == 1
        assert nodes[0]["name"] == "部门A"
        assert len(nodes[0]["children"]) == 1
        assert nodes[0]["children"][0]["name"] == "子部门A1"
        assert nodes[0]["children"][0]["children"][0]["name"] == "小组A1a"

    async def test_tree_user_count(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "display_name": "Alice", "department_id": a["id"],
            },
        )
        resp = await admin_client.get("/api/v1/departments/tree")
        assert resp.json()["nodes"][0]["user_count"] == 1


@pytest.mark.asyncio
class TestGetDepartment:
    async def test_get_existing(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        resp = await admin_client.get(f"/api/v1/departments/{a['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "部门A"

    async def test_get_nonexistent(self, admin_client: AsyncClient):
        resp = await admin_client.get("/api/v1/departments/dept-nope")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestRename:
    async def test_rename_success(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        resp = await admin_client.patch(
            f"/api/v1/departments/{a['id']}", json={"name": "部门A_改"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "部门A_改"

    async def test_rename_to_existing_name_conflict(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/departments", json={"name": "部门A"})
        b = (await admin_client.post("/api/v1/departments", json={"name": "部门B"})).json()
        resp = await admin_client.patch(
            f"/api/v1/departments/{b['id']}", json={"name": "部门A"}
        )
        assert resp.status_code == 409

    async def test_rename_noop_returns_200(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        resp = await admin_client.patch(
            f"/api/v1/departments/{a['id']}", json={"name": "部门A"}
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestMove:
    async def test_move_to_root(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        a1 = (await admin_client.post(
            "/api/v1/departments", json={"name": "子部门A1", "parent_id": a["id"]}
        )).json()
        resp = await admin_client.post(
            f"/api/v1/departments/{a1['id']}/move",
            json={"new_parent_id": None},
        )
        assert resp.status_code == 200
        assert resp.json()["parent_id"] is None

    async def test_move_under_other_parent(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        b = (await admin_client.post("/api/v1/departments", json={"name": "部门B"})).json()
        a1 = (await admin_client.post(
            "/api/v1/departments", json={"name": "子部门A1", "parent_id": a["id"]}
        )).json()
        resp = await admin_client.post(
            f"/api/v1/departments/{a1['id']}/move",
            json={"new_parent_id": b["id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["parent_id"] == b["id"]

    async def test_cycle_under_self(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        resp = await admin_client.post(
            f"/api/v1/departments/{a['id']}/move",
            json={"new_parent_id": a["id"]},
        )
        assert resp.status_code == 400

    async def test_cycle_under_descendant(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        a1 = (await admin_client.post(
            "/api/v1/departments", json={"name": "子部门A1", "parent_id": a["id"]}
        )).json()
        a1a = (await admin_client.post(
            "/api/v1/departments", json={"name": "小组A1a", "parent_id": a1["id"]}
        )).json()
        # Try to move A under its grandchild
        resp = await admin_client.post(
            f"/api/v1/departments/{a['id']}/move",
            json={"new_parent_id": a1a["id"]},
        )
        assert resp.status_code == 400

    async def test_move_name_conflict_at_new_parent(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        b = (await admin_client.post("/api/v1/departments", json={"name": "部门B"})).json()
        # Both A and B already have a child named "子部门"
        await admin_client.post(
            "/api/v1/departments", json={"name": "子部门", "parent_id": a["id"]}
        )
        b_child = (await admin_client.post(
            "/api/v1/departments", json={"name": "子部门", "parent_id": b["id"]}
        )).json()
        # Try to move B's "子部门" under A — collides with A's existing 子部门
        resp = await admin_client.post(
            f"/api/v1/departments/{b_child['id']}/move",
            json={"new_parent_id": a["id"]},
        )
        assert resp.status_code == 409


@pytest.mark.asyncio
class TestDelete:
    async def test_delete_empty_dept(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        resp = await admin_client.delete(f"/api/v1/departments/{a['id']}")
        assert resp.status_code == 204
        assert (await admin_client.get(f"/api/v1/departments/{a['id']}")).status_code == 404

    async def test_delete_with_children_blocked(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        await admin_client.post(
            "/api/v1/departments", json={"name": "子部门A1", "parent_id": a["id"]}
        )
        resp = await admin_client.delete(f"/api/v1/departments/{a['id']}")
        assert resp.status_code == 409
        body = resp.json()["detail"]
        assert body["child_count"] == 1
        assert body["user_count"] == 0

    async def test_delete_with_users_blocked(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": a["id"],
            },
        )
        resp = await admin_client.delete(f"/api/v1/departments/{a['id']}")
        assert resp.status_code == 409
        assert resp.json()["detail"]["user_count"] == 1


@pytest.mark.asyncio
class TestResolve:
    async def test_resolve_empty_path(self, admin_client: AsyncClient):
        resp = await admin_client.post("/api/v1/departments/resolve", json={"path": []})
        assert resp.status_code == 200
        assert resp.json()["id"] is None

    async def test_resolve_blank_path(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/departments/resolve", json={"path": ["", "  "]}
        )
        assert resp.status_code == 200
        assert resp.json()["id"] is None

    async def test_resolve_creates_chain(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/departments/resolve",
            json={"path": ["部门A", "子部门A1", "小组A1a"]},
        )
        assert resp.status_code == 200
        leaf_id = resp.json()["id"]
        assert leaf_id is not None
        # Verify the chain via GET tree
        tree = (await admin_client.get("/api/v1/departments/tree")).json()
        assert tree["nodes"][0]["name"] == "部门A"
        assert tree["nodes"][0]["children"][0]["name"] == "子部门A1"
        assert tree["nodes"][0]["children"][0]["children"][0]["id"] == leaf_id

    async def test_resolve_idempotent(self, admin_client: AsyncClient):
        first = (await admin_client.post(
            "/api/v1/departments/resolve",
            json={"path": ["部门A", "子部门A1"]},
        )).json()["id"]
        second = (await admin_client.post(
            "/api/v1/departments/resolve",
            json={"path": ["部门A", "子部门A1"]},
        )).json()["id"]
        assert first == second


@pytest.mark.asyncio
class TestUserDepartmentIntegration:
    async def test_create_user_with_dept(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": a["id"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["department_id"] == a["id"]

    async def test_create_user_with_invalid_dept(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": "dept-nope",
            },
        )
        assert resp.status_code == 400

    async def test_update_user_department(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        b = (await admin_client.post("/api/v1/departments", json={"name": "部门B"})).json()
        u = (await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": a["id"],
            },
        )).json()
        resp = await admin_client.put(
            f"/api/v1/admin/users/{u['id']}",
            json={"department_id": b["id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["department_id"] == b["id"]

    async def test_clear_user_department(self, admin_client: AsyncClient):
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        u = (await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": a["id"],
            },
        )).json()
        # Explicit null clears
        resp = await admin_client.put(
            f"/api/v1/admin/users/{u['id']}",
            json={"department_id": None},
        )
        assert resp.status_code == 200
        assert resp.json()["department_id"] is None

    async def test_update_without_dept_field_preserves(self, admin_client: AsyncClient):
        """字段缺省 ≠ 清空 — 不传 department_id 时应当保持原值。"""
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        u = (await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": a["id"],
            },
        )).json()
        # Update only display_name — dept should be unchanged
        resp = await admin_client.put(
            f"/api/v1/admin/users/{u['id']}",
            json={"display_name": "Alice Updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["department_id"] == a["id"]

    async def test_list_users_includes_department_id(self, admin_client: AsyncClient):
        """
        Regression: list_users 响应里 department_id 不应丢。

        UserResponse schema 含 department_id；单查 / 创建 / 更新都返回该字段；
        早期 list_users 端点的响应构造里漏了 — 导致前端 UserRow 永远拿到 null。
        """
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": a["id"],
            },
        )
        r = await admin_client.get("/api/v1/admin/users")
        assert r.status_code == 200
        alice = next(u for u in r.json()["users"] if u["username"] == "alice")
        assert alice["department_id"] == a["id"]

    async def test_delete_dept_clears_user_dept(self, admin_client: AsyncClient):
        """ondelete=SET NULL：删除部门时其下用户的 department_id 自动置空。"""
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        u = (await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": "alice", "password": "Pass1234!",
                "department_id": a["id"],
            },
        )).json()
        # Move user out so dept becomes empty (delete check requires user_count=0)
        await admin_client.put(
            f"/api/v1/admin/users/{u['id']}", json={"department_id": None}
        )
        # Now safe to delete dept
        resp = await admin_client.delete(f"/api/v1/departments/{a['id']}")
        assert resp.status_code == 204
        # Verify user still exists
        u_after = (await admin_client.get(f"/api/v1/admin/users/{u['id']}")).json()
        assert u_after["department_id"] is None


@pytest.mark.asyncio
class TestUserSearchSubtree:
    """Phase 3 — 用户搜索按部门子树扩展。"""

    async def _setup(self, admin_client: AsyncClient):
        # Tree: 部门A → 子部门A1 → 小组A1a; 部门B
        a = (await admin_client.post("/api/v1/departments", json={"name": "部门A"})).json()
        a1 = (await admin_client.post(
            "/api/v1/departments", json={"name": "子部门A1", "parent_id": a["id"]}
        )).json()
        a1a = (await admin_client.post(
            "/api/v1/departments", json={"name": "小组A1a", "parent_id": a1["id"]}
        )).json()
        b = (await admin_client.post("/api/v1/departments", json={"name": "部门B"})).json()
        users = [
            ("alice", "Alice", a["id"]),
            ("bob", "Bob", a1["id"]),
            ("carol", "Carol", a1a["id"]),
            ("dave", "Dave", b["id"]),
            ("eve", "Eve", None),
        ]
        for uname, dn, dept_id in users:
            await admin_client.post(
                "/api/v1/admin/users",
                json={
                    "username": uname, "password": "Pass1234!",
                    "display_name": dn, "department_id": dept_id,
                },
            )
        return a, a1, a1a, b

    async def test_search_root_returns_subtree(self, admin_client: AsyncClient):
        await self._setup(admin_client)
        r = await admin_client.get("/api/v1/admin/users", params={"q": "部门A"})
        usernames = sorted(u["username"] for u in r.json()["users"])
        assert usernames == ["alice", "bob", "carol"]

    async def test_search_leaf_returns_direct_only(self, admin_client: AsyncClient):
        await self._setup(admin_client)
        r = await admin_client.get("/api/v1/admin/users", params={"q": "小组A1a"})
        usernames = sorted(u["username"] for u in r.json()["users"])
        assert usernames == ["carol"]

    async def test_search_mid_level(self, admin_client: AsyncClient):
        await self._setup(admin_client)
        r = await admin_client.get("/api/v1/admin/users", params={"q": "子部门A1"})
        usernames = sorted(u["username"] for u in r.json()["users"])
        assert usernames == ["bob", "carol"]

    async def test_search_display_name_still_works(self, admin_client: AsyncClient):
        await self._setup(admin_client)
        r = await admin_client.get("/api/v1/admin/users", params={"q": "Bob"})
        usernames = sorted(u["username"] for u in r.json()["users"])
        assert usernames == ["bob"]

    async def test_search_username_still_works(self, admin_client: AsyncClient):
        await self._setup(admin_client)
        r = await admin_client.get("/api/v1/admin/users", params={"q": "eve"})
        usernames = sorted(u["username"] for u in r.json()["users"])
        assert usernames == ["eve"]

    async def test_search_no_match(self, admin_client: AsyncClient):
        await self._setup(admin_client)
        r = await admin_client.get(
            "/api/v1/admin/users", params={"q": "noSuchUser_or_dept_xyz"}
        )
        assert r.json()["users"] == []

    async def test_search_count_for_subtree(self, admin_client: AsyncClient):
        await self._setup(admin_client)
        r = await admin_client.get(
            "/api/v1/admin/users", params={"q": "部门A", "limit": 1}
        )
        # total spans full subtree even if limit=1
        assert r.json()["total"] == 3

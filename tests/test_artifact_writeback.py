"""
ArtifactManager write-back contract tests.

Covers the design invariant: execution-time edits are memory-only,
flush_all persists a single final snapshot per artifact, and version
numbers may be sparse (intermediate in-memory versions are not recorded).
"""

import uuid

import pytest

from db.models import User
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from tools.builtin.artifact_ops import ArtifactManager


@pytest.fixture
async def session_id(conversation_repo: ConversationRepository, test_user: User) -> str:
    """Create a conversation (auto-creates ArtifactSession), return session_id."""
    conv_id = f"conv-{uuid.uuid4().hex}"
    await conversation_repo.create_conversation(
        conversation_id=conv_id, user_id=test_user.id
    )
    return conv_id


@pytest.fixture
def artifact_manager(artifact_repo: ArtifactRepository) -> ArtifactManager:
    return ArtifactManager(artifact_repo)


class TestReadArtifactInMemoryVersion:
    """显式 version=N 读取需要识别 in-memory 当前版本，否则刚持久化但未 flush
    的 artifact 用 envelope 里看到的 version=1 调用会 404。
    """

    async def test_explicit_version_matches_in_memory(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """刚创建未 flush 的 artifact，version=1 读取应命中内存。"""
        artifact_manager.set_session(session_id)
        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id, artifact_id="doc1",
            content_type="text/plain", title="T", content="hello",
        )
        assert ok

        # 显式 version=1（envelope 里看到的版本号）应返回内存内容
        result = await artifact_manager.read_artifact(
            session_id=session_id, artifact_id="doc1", version=1
        )
        assert result is not None
        assert result["content"] == "hello"
        assert result["version"] == 1

    async def test_explicit_version_after_flush(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """flush 后显式 version=1 走 DB 路径，仍能拿到内容。"""
        artifact_manager.set_session(session_id)
        await artifact_manager.create_artifact(
            session_id=session_id, artifact_id="doc2",
            content_type="text/plain", title="T", content="v1 content",
        )
        await artifact_manager.flush_all(session_id)

        result = await artifact_manager.read_artifact(
            session_id=session_id, artifact_id="doc2", version=1
        )
        assert result is not None
        assert result["content"] == "v1 content"

    async def test_explicit_nonexistent_version_returns_none(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """请求一个从未存在过的版本号 → None（404）。"""
        artifact_manager.set_session(session_id)
        await artifact_manager.create_artifact(
            session_id=session_id, artifact_id="doc3",
            content_type="text/plain", title="T", content="x",
        )

        # 内存里只有 v1，请求 v99 应该是 None
        result = await artifact_manager.read_artifact(
            session_id=session_id, artifact_id="doc3", version=99
        )
        assert result is None


class TestPersistToolResult:
    """persist_tool_result 必须扛住任意 tool_name（长名 / 非法字符）。

    回归测试：早期 ID 校验加上后，长 tool_name 会让 persist_tool_result
    生成超 64 字符的 ID 然后 RuntimeError，引擎中间件 fail-open 把原始
    超长内容塞回 context——这恰恰是该机制要防的。
    """

    async def test_long_tool_name(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        artifact_manager.set_session(session_id)
        long_name = "very_long_custom_http_tool_name_" * 3  # ~96 chars
        aid, version = await artifact_manager.persist_tool_result(
            session_id=session_id, tool_name=long_name, content="x" * 1000,
        )
        assert len(aid) <= 64
        assert aid.startswith("tool_")
        assert version == 1

    async def test_tool_name_with_special_chars(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """MCP 工具名常含 `:`、`.`，自定义工具可能含 `/` 等。"""
        artifact_manager.set_session(session_id)
        names = [
            "mcp:server:tool",
            "mcp__github__create_issue",
            "weird/tool name with spaces",
            "tool.with.dots",
        ]
        for name in names:
            aid, _ = await artifact_manager.persist_tool_result(
                session_id=session_id, tool_name=name, content="x",
            )
            # ID 通过 create_artifact 校验意味着只含 [\w\-.]
            import re
            assert re.match(r"^[\w\-.]{1,64}$", aid), f"invalid id for {name!r}: {aid}"

    async def test_short_tool_name_unchanged_in_id(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        artifact_manager.set_session(session_id)
        aid, _ = await artifact_manager.persist_tool_result(
            session_id=session_id, tool_name="web_fetch", content="x",
        )
        assert aid.startswith("tool_web_fetch_")

    async def test_metadata_preserves_original_tool_name(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """sanitize 后的名字进 ID，但原始名字必须留在 metadata 里供审计。"""
        artifact_manager.set_session(session_id)
        original = "mcp:server:tool"
        aid, _ = await artifact_manager.persist_tool_result(
            session_id=session_id, tool_name=original, content="x",
        )
        # 从 manager 缓存里读 metadata
        memory = artifact_manager._cache[session_id][aid]
        assert memory.metadata["tool_name"] == original


class TestCreateFromUpload:
    """create_from_upload 也必须满足 _ARTIFACT_ID_PATTERN（之前漏校验，
    长文件名会让 ID 超 64 字符进 DB）。"""

    async def test_long_filename_normalized(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        # 80 字符的 base name + .txt 扩展名
        long_filename = ("a" * 80) + ".txt"
        ok, _, info = await artifact_manager.create_from_upload(
            session_id=session_id, filename=long_filename,
            content="hello", content_type="text/plain",
        )
        assert ok
        aid = info["id"]
        assert len(aid) <= 64
        # 扩展名应被保留
        assert aid.endswith(".txt")
        import re
        assert re.match(r"^[\w\-.]{1,64}$", aid)

    async def test_filename_with_special_chars(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        ok, _, info = await artifact_manager.create_from_upload(
            session_id=session_id, filename="report (final) v2!.txt",
            content="x", content_type="text/plain",
        )
        assert ok
        import re
        assert re.match(r"^[\w\-.]{1,64}$", info["id"])

    async def test_dedup_suffix_stays_within_cap(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """长文件名连续上传同名，dedup 后 ID 仍 ≤ 64。"""
        long_filename = ("b" * 70) + ".txt"
        ids = []
        for _ in range(3):
            ok, _, info = await artifact_manager.create_from_upload(
                session_id=session_id, filename=long_filename,
                content="x", content_type="text/plain",
            )
            assert ok, info
            assert len(info["id"]) <= 64
            ids.append(info["id"])
        # 三个 ID 必须互不相同
        assert len(set(ids)) == 3

    async def test_all_punctuation_filename(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """全标点文件名 → 全部变 _，仍合法（_ 是 \\w）不触发 'upload' fallback。"""
        ok, _, info = await artifact_manager.create_from_upload(
            session_id=session_id, filename="!!!@@@",
            content="x", content_type="text/plain",
        )
        assert ok
        import re
        assert re.match(r"^[\w\-.]{1,64}$", info["id"])
        assert info["id"] == "______"  # 6 个 _

    async def test_empty_filename_sanitized(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """空文件名 → 走 fallback 'upload'。"""
        ok, _, info = await artifact_manager.create_from_upload(
            session_id=session_id, filename="",
            content="x", content_type="text/plain",
        )
        assert ok
        assert info["id"] == "upload"

    async def test_chinese_filename_preserved(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """中文文件名：Python 3 默认 \\w 是 Unicode-aware，中文应被保留。

        Regression guard：如果以后有人加了 re.ASCII 或改了正则，中文会
        全部变成 _，让所有中文上传变成相同 ID 互相 dedup 冲突。
        """
        ok, _, info = await artifact_manager.create_from_upload(
            session_id=session_id, filename="季度报告.txt",
            content="x", content_type="text/plain",
        )
        assert ok
        # 中文字符必须保留，不能变 _
        assert "季度报告" in info["id"]
        assert info["id"].endswith(".txt")
        # 同时仍满足 ID pattern
        import re
        assert re.match(r"^[\w\-.]{1,64}$", info["id"])

    async def test_chinese_filename_with_punctuation(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """中文 + 标点符号：中文保留，全角 / 半角标点变 _。"""
        ok, _, info = await artifact_manager.create_from_upload(
            session_id=session_id, filename="报告（V2）.txt",
            content="x", content_type="text/plain",
        )
        assert ok
        # 中文保留，全角括号变 _
        assert "报告" in info["id"]
        assert "v2" in info["id"]  # .lower() 把 V2 → v2
        assert "（" not in info["id"]
        assert "）" not in info["id"]


class TestArtifactIdValidation:
    """Layer A: create_artifact 校验 id，避免脏字符流入 envelope attribute。"""

    @pytest.mark.parametrize("bad_id", [
        'evil"id',          # 引号会破 envelope attribute 边界
        "with space",       # 空格
        "with<gt",          # 角括号
        "with&amp",         # & 字符
        "",                 # 空串
        "x" * 65,           # 超长（上限 64）
    ])
    async def test_invalid_id_rejected(
        self, artifact_manager: ArtifactManager, session_id: str, bad_id: str,
    ):
        artifact_manager.set_session(session_id)
        ok, msg = await artifact_manager.create_artifact(
            session_id=session_id, artifact_id=bad_id,
            content_type="text/plain", title="t", content="x",
        )
        assert not ok, f"expected reject for {bad_id!r}"
        assert "Invalid artifact_id" in msg

    @pytest.mark.parametrize("good_id", [
        "task_plan",
        "doc-1",
        "report.v2",
        "tool_web_fetch_a3b9c1d2e4f5",
        "x",  # 单字符
        "x" * 64,  # 上限
    ])
    async def test_valid_id_accepted(
        self, artifact_manager: ArtifactManager, session_id: str, good_id: str,
    ):
        artifact_manager.set_session(session_id)
        ok, msg = await artifact_manager.create_artifact(
            session_id=session_id, artifact_id=good_id,
            content_type="text/plain", title="t", content="x",
        )
        assert ok, msg


class TestWriteBackFlush:
    """Verify that flush_all collapses in-memory edits into a single DB version."""

    async def test_create_then_updates_produce_single_version(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """create -> update -> update -> flush produces one version record at v3."""
        artifact_manager.set_session(session_id)

        # In-memory create (v1)
        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            content_type="text/markdown",
            title="Plan",
            content="# Step 1",
        )
        assert ok

        # In-memory update (v2)
        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            old_str="# Step 1",
            new_str="# Step 1\n# Step 2",
        )
        assert ok

        # In-memory update (v3)
        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            old_str="# Step 1\n# Step 2",
            new_str="# Step 1\n# Step 2\n# Step 3",
        )
        assert ok

        # Verify memory state
        memory = await artifact_manager.get_artifact(session_id, "task_plan")
        assert memory is not None
        assert memory.current_version == 3

        # DB should have nothing yet
        db_art = await artifact_repo.get_artifact(session_id, "task_plan")
        assert db_art is None

        # Flush
        await artifact_manager.flush_all(session_id)

        # DB should now have the artifact at v3
        db_art = await artifact_repo.get_artifact(session_id, "task_plan")
        assert db_art is not None
        assert db_art.current_version == 3
        assert db_art.content == "# Step 1\n# Step 2\n# Step 3"

        # Only one version record should exist (the final snapshot)
        versions = await artifact_repo.list_versions(session_id, "task_plan")
        assert len(versions) == 1
        assert versions[0].version == 3
        assert versions[0].update_type == "create"
        assert versions[0].content == "# Step 1\n# Step 2\n# Step 3"

    async def test_existing_artifact_update_flush(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """Pre-existing artifact updated twice in-memory flushes as one new version."""
        # Pre-create in DB (v1)
        await artifact_repo.create_artifact(
            session_id=session_id,
            artifact_id="report",
            content_type="text/markdown",
            title="Report",
            content="initial",
        )

        artifact_manager.set_session(session_id)

        # Two in-memory updates (v2, v3)
        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="initial",
            new_str="updated once",
        )
        assert ok

        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="updated once",
            new_str="updated twice",
        )
        assert ok

        await artifact_manager.flush_all(session_id)

        db_art = await artifact_repo.get_artifact(session_id, "report")
        assert db_art.current_version == 3
        assert db_art.content == "updated twice"

        # Two version records: v1 (original create) + v3 (flushed update)
        # v2 is skipped — sparse version numbers are by design
        versions = await artifact_repo.list_versions(session_id, "report")
        assert len(versions) == 2
        assert [v.version for v in versions] == [1, 3]

    async def test_flush_is_idempotent(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """Calling flush_all twice does not create duplicate records."""
        artifact_manager.set_session(session_id)

        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="doc",
            content_type="text/markdown",
            title="Doc",
            content="hello",
        )
        assert ok

        await artifact_manager.flush_all(session_id)
        await artifact_manager.flush_all(session_id)  # no-op

        versions = await artifact_repo.list_versions(session_id, "doc")
        assert len(versions) == 1


class TestWriteBackInventory:
    """Verify that list_artifacts merges in-memory state during execution."""

    async def test_list_includes_unflushed_new_artifact(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """New in-memory artifact appears in list_artifacts before flush."""
        artifact_manager.set_session(session_id)

        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="plan",
            content_type="text/markdown",
            title="Plan",
            content="# Plan",
        )
        assert ok

        artifacts = await artifact_manager.list_artifacts(session_id)
        assert len(artifacts) == 1
        assert artifacts[0]["id"] == "plan"
        assert artifacts[0]["content"] == "# Plan"

    async def test_list_preserves_insertion_order_for_unflushed(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """In-memory new artifacts must come back in creation order.

        Regression: `_new` used to be a `set()` whose iteration is hash-ordered,
        not insertion-ordered — so session-scope consumers (grep session-mode cap
        truncation, inventory rendering) saw a non-deterministic order across runs.
        """
        artifact_manager.set_session(session_id)
        # Use 10 IDs whose hash order is essentially guaranteed to differ
        # from creation order — short alphanumeric strings reshuffle under
        # PYTHONHASHSEED randomization. 10 entries makes accidental same-order
        # vanishingly unlikely.
        ids = [f"art_{i:02d}" for i in range(10)]
        for aid in ids:
            ok, _ = await artifact_manager.create_artifact(
                session_id=session_id,
                artifact_id=aid,
                content_type="text/plain",
                title=aid,
                content=f"body of {aid}",
            )
            assert ok

        artifacts = await artifact_manager.list_artifacts(session_id)
        assert [a["id"] for a in artifacts] == ids

    async def test_list_shows_dirty_content_over_db(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """In-memory edits override DB content in list_artifacts."""
        # Pre-create in DB
        await artifact_repo.create_artifact(
            session_id=session_id,
            artifact_id="report",
            content_type="text/markdown",
            title="Report",
            content="old content",
        )

        artifact_manager.set_session(session_id)

        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="old content",
            new_str="new content",
        )
        assert ok

        artifacts = await artifact_manager.list_artifacts(session_id)
        assert len(artifacts) == 1
        assert artifacts[0]["content"] == "new content"
        assert artifacts[0]["version"] == 2


class TestWriteBackFlushFailure:
    """Verify that failed flushes retain dirty state."""

    async def test_failed_flush_keeps_dirty(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """If flush fails for one artifact, it stays in dirty set."""
        artifact_manager.set_session(session_id)

        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="will_fail",
            content_type="text/markdown",
            title="Fail",
            content="content",
        )
        assert ok

        # Sabotage: pre-create the same artifact in DB so flush hits DuplicateError
        await artifact_repo.create_artifact(
            session_id=session_id,
            artifact_id="will_fail",
            content_type="text/markdown",
            title="Existing",
            content="existing",
        )

        with pytest.raises(RuntimeError, match="Failed to flush"):
            await artifact_manager.flush_all(session_id)

        # Dirty entry should still be present
        assert (session_id, "will_fail") in artifact_manager._dirty

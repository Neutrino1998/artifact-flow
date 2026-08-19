"""
ArtifactService write-back contract tests.

Covers the design invariant: execution-time edits are memory-only,
flush_all persists a single final snapshot per artifact, and version
numbers may be sparse (intermediate in-memory versions are not recorded).
"""

import uuid

import pytest
from sqlalchemy import select

from config import config
from db.models import ArtifactBlob, User
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from tools.base import ArtifactSpec
from tools.builtin.artifact_service import ArtifactService


async def _persist_blob(
    artifact_service: ArtifactService, session_id: str, name: str, size: int
):
    """Stage a blob-backed artifact the way sandbox persist does (source=sandbox)."""
    return await artifact_service.create_from_upload(
        session_id=session_id,
        filename=name,
        content="",
        content_type="application/octet-stream",
        blob=b"x" * size,
        source="sandbox",
    )


@pytest.fixture
async def session_id(conversation_repo: ConversationRepository, test_user: User) -> str:
    """Create a conversation (auto-creates ArtifactSession), return session_id."""
    conv_id = f"conv-{uuid.uuid4().hex}"
    await conversation_repo.create_conversation(
        conversation_id=conv_id, user_id=test_user.id
    )
    return conv_id


@pytest.fixture
def artifact_service(artifact_repo: ArtifactRepository) -> ArtifactService:
    return ArtifactService(artifact_repo)


class TestReadArtifactInMemoryVersion:
    """显式 version=N 读取需要识别 in-memory 当前版本，否则刚持久化但未 flush
    的 artifact 用 envelope 里看到的 version=1 调用会 404。
    """

    async def test_explicit_version_matches_in_memory(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """刚创建未 flush 的 artifact，version=1 读取应命中内存。"""
        artifact_service.set_session(session_id)
        ok, _ = await artifact_service.create_artifact(
            session_id=session_id, artifact_id="doc1",
            content_type="text/plain", title="T", content="hello",
        )
        assert ok

        # 显式 version=1（envelope 里看到的版本号）应返回内存内容
        result = await artifact_service.read_artifact(
            session_id=session_id, artifact_id="doc1", version=1
        )
        assert result is not None
        assert result["content"] == "hello"
        assert result["version"] == 1

    async def test_explicit_version_after_flush(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """flush 后显式 version=1 走 DB 路径，仍能拿到内容。"""
        artifact_service.set_session(session_id)
        await artifact_service.create_artifact(
            session_id=session_id, artifact_id="doc2",
            content_type="text/plain", title="T", content="v1 content",
        )
        await artifact_service.flush_all(session_id)

        result = await artifact_service.read_artifact(
            session_id=session_id, artifact_id="doc2", version=1
        )
        assert result is not None
        assert result["content"] == "v1 content"

    async def test_explicit_nonexistent_version_returns_none(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """请求一个从未存在过的版本号 → None（404）。"""
        artifact_service.set_session(session_id)
        await artifact_service.create_artifact(
            session_id=session_id, artifact_id="doc3",
            content_type="text/plain", title="T", content="x",
        )

        # 内存里只有 v1，请求 v99 应该是 None
        result = await artifact_service.read_artifact(
            session_id=session_id, artifact_id="doc3", version=99
        )
        assert result is None


class TestIngestToolResult:
    """ingest_tool_result（具名 + 无名兜底共用)必须扛住任意 tool_name（长名 / 非法
    字符），生成的 ID 始终满足 _ARTIFACT_ID_PATTERN。

    回归：早期 ID 校验加上后，长/非法 tool_name 会让生成的 ID 超 64 字符 / 含非法
    字符然后落盘失败，引擎中间件 fail-open 把原始超长内容塞回 context——这正是该
    机制要防的。现在 tool_name 经 `<tool_name>_output` → _normalize_filename_to_id
    收口（截断 + sanitize）。
    """

    async def test_long_tool_name(
        self, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        long_name = "very_long_custom_http_tool_name_" * 3  # ~96 chars
        spec = ArtifactSpec(content_type="text/plain", content="x" * 1000)
        ok, _, aid = await artifact_service.ingest_tool_result(
            session_id=session_id, spec=spec, tool_name=long_name,
        )
        assert ok
        assert len(aid) <= 64

    async def test_tool_name_with_special_chars(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """MCP 工具名常含 `:`、`.`，自定义工具可能含 `/` 等。"""
        artifact_service.set_session(session_id)
        names = [
            "mcp:server:tool",
            "mcp__github__create_issue",
            "weird/tool name with spaces",
            "tool.with.dots",
        ]
        import re
        for name in names:
            spec = ArtifactSpec(content_type="text/plain", content="x")
            ok, _, aid = await artifact_service.ingest_tool_result(
                session_id=session_id, spec=spec, tool_name=name,
            )
            assert ok
            # 生成的 ID 只含 [\w\-.]
            assert re.match(r"^[\w\-.]{1,64}$", aid), f"invalid id for {name!r}: {aid}"

    async def test_short_tool_name_in_id(
        self, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        spec = ArtifactSpec(content_type="text/plain", content="x")
        ok, _, aid = await artifact_service.ingest_tool_result(
            session_id=session_id, spec=spec, tool_name="web_fetch",
        )
        assert ok
        assert aid.startswith("web_fetch")

    async def test_metadata_preserves_original_tool_name(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """原始工具名必须留在 metadata 里供审计。"""
        artifact_service.set_session(session_id)
        original = "mcp:server:tool"
        spec = ArtifactSpec(content_type="text/plain", content="x")
        ok, _, aid = await artifact_service.ingest_tool_result(
            session_id=session_id, spec=spec, tool_name=original,
        )
        assert ok
        memory = artifact_service.working_set.peek(session_id, aid)
        assert memory.metadata["tool_name"] == original

    async def test_named_spec_with_filename_and_blob(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """具名:filename 驱动 id + 下载名,blob 落入 memory,has_blob 置真。"""
        artifact_service.set_session(session_id)
        spec = ArtifactSpec(
            content_type="application/pdf",
            filename="report.pdf",
            blob=b"%PDF-1.4 fake",
            metadata={"source_url": "https://example.com/report.pdf"},
        )
        ok, _, aid = await artifact_service.ingest_tool_result(
            session_id=session_id, spec=spec, tool_name="web_fetch",
        )
        assert ok
        assert aid == "report.pdf"
        memory = artifact_service.working_set.peek(session_id, aid)
        assert memory.blob == b"%PDF-1.4 fake"
        assert memory.has_blob is True
        assert "blob_content_type" not in memory.metadata  # 不再塞 metadata
        assert memory.metadata["original_filename"] == "report.pdf"
        assert memory.metadata["source_url"] == "https://example.com/report.pdf"
        assert memory.source == "tool"

    async def test_refetch_same_filename_dedups(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """同名两次 → _N 去重建新 artifact(对齐上传行为)。"""
        artifact_service.set_session(session_id)
        spec = ArtifactSpec(content_type="application/pdf", filename="report.pdf", blob=b"a")
        ok1, _, aid1 = await artifact_service.ingest_tool_result(
            session_id=session_id, spec=spec, tool_name="web_fetch",
        )
        ok2, _, aid2 = await artifact_service.ingest_tool_result(
            session_id=session_id, spec=spec, tool_name="web_fetch",
        )
        assert ok1 and ok2
        assert aid1 == "report.pdf"
        assert aid2 == "report_1.pdf"


class TestXorInvariant:
    """一个 artifact 只存一份实质 data:content XOR blob,两者皆有 = loud-fail
    (经共享 _stage_artifact 守门,覆盖 ingest_tool_result + create_from_upload)。"""

    async def test_ingest_rejects_both_content_and_blob(
        self, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        spec = ArtifactSpec(
            content_type="application/pdf",
            filename="x.pdf",
            content="some text",  # 不该和 blob 并存
            blob=b"%PDF",
        )
        ok, message, aid = await artifact_service.ingest_tool_result(
            session_id=session_id, spec=spec, tool_name="t",
        )
        assert not ok
        assert aid is None
        assert "never both" in message

    async def test_upload_rejects_both_content_and_blob(
        self, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        ok, message, info = await artifact_service.create_from_upload(
            session_id=session_id, filename="x.pdf",
            content="text", content_type="application/pdf",
            blob=b"%PDF",
        )
        assert not ok
        assert info is None
        assert "never both" in message

    async def test_blob_only_and_text_only_both_ok(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """边界:blob + 空 content = blob-only(放行);text + 无 blob = text(放行)。"""
        artifact_service.set_session(session_id)
        ok_blob, _, aid_blob = await artifact_service.ingest_tool_result(
            session_id=session_id,
            spec=ArtifactSpec(content_type="application/pdf", filename="a.pdf", blob=b"%PDF"),
            tool_name="t",
        )
        ok_text, _, aid_text = await artifact_service.ingest_tool_result(
            session_id=session_id,
            spec=ArtifactSpec(content_type="text/csv", filename="a.csv", content="a,b"),
            tool_name="t",
        )
        assert ok_blob and aid_blob == "a.pdf"
        assert ok_text and aid_text == "a.csv"


class TestTextSizeAdmission:
    """所有文本 Artifact 创建/修改路径共享 UTF-8 字节上限。"""

    async def test_create_upload_and_tool_result_share_text_limit(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        monkeypatch.setattr(config, "ARTIFACT_TEXT_MAX_BYTES", 4)
        artifact_service.set_session(session_id)

        # 上限按 UTF-8 bytes 而非 Python chars；两个 é 恰好四字节。
        ok, message = await artifact_service.create_artifact(
            session_id, "exact", "text/plain", "Exact", "éé"
        )
        assert ok, message

        ok, message = await artifact_service.create_artifact(
            session_id, "agent_too_large", "text/plain", "Large", "ééx"
        )
        assert not ok
        assert "Text artifact too large" in message

        # 普通工具溢出兜底与显式 text ArtifactSpec 都经 ingest_tool_result。
        ok, message, aid = await artifact_service.ingest_tool_result(
            session_id,
            ArtifactSpec(content_type="text/plain", content="12345"),
            tool_name="remote_tool",
        )
        assert not ok
        assert aid is None
        assert "Text artifact too large" in message

        ok, message, info = await artifact_service.create_from_upload(
            session_id=session_id,
            filename="upload.txt",
            content="12345",
            content_type="text/plain",
        )
        assert not ok
        assert info is None
        assert "Text artifact too large" in message

    async def test_rewrite_and_update_reject_before_mutating_working_set(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        monkeypatch.setattr(config, "ARTIFACT_TEXT_MAX_BYTES", 4)
        artifact_service.set_session(session_id)
        ok, message = await artifact_service.create_artifact(
            session_id, "doc", "text/plain", "Doc", "1234"
        )
        assert ok, message

        ok, message = await artifact_service.rewrite_artifact(
            session_id, "doc", "12345"
        )
        assert not ok
        assert "Text artifact too large" in message

        ok, message, metadata = await artifact_service.update_artifact(
            session_id, "doc", "4", "45"
        )
        assert not ok
        assert metadata is None
        assert "Text artifact too large" in message

        memory = artifact_service.working_set.peek(session_id, "doc")
        assert memory.content == "1234"
        assert memory.current_version == 1

    async def test_text_limit_does_not_reclassify_or_reject_blob(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        monkeypatch.setattr(config, "ARTIFACT_TEXT_MAX_BYTES", 1)
        ok, message, aid = await artifact_service.ingest_tool_result(
            session_id,
            ArtifactSpec(
                content_type="application/octet-stream",
                filename="payload.bin",
                blob=b"12345",
            ),
            tool_name="remote_tool",
        )
        assert ok, message
        assert aid == "payload.bin"


class TestCreateFromUpload:
    """create_from_upload 也必须满足 _ARTIFACT_ID_PATTERN（之前漏校验，
    长文件名会让 ID 超 64 字符进 DB）。"""

    async def test_long_filename_normalized(
        self, artifact_service: ArtifactService, session_id: str
    ):
        # 80 字符的 base name + .txt 扩展名
        long_filename = ("a" * 80) + ".txt"
        ok, _, info = await artifact_service.create_from_upload(
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
        self, artifact_service: ArtifactService, session_id: str
    ):
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id, filename="report (final) v2!.txt",
            content="x", content_type="text/plain",
        )
        assert ok
        import re
        assert re.match(r"^[\w\-.]{1,64}$", info["id"])

    async def test_dedup_suffix_stays_within_cap(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """长文件名连续上传同名，dedup 后 ID 仍 ≤ 64。"""
        long_filename = ("b" * 70) + ".txt"
        ids = []
        for _ in range(3):
            ok, _, info = await artifact_service.create_from_upload(
                session_id=session_id, filename=long_filename,
                content="x", content_type="text/plain",
            )
            assert ok, info
            assert len(info["id"]) <= 64
            ids.append(info["id"])
        # 三个 ID 必须互不相同
        assert len(set(ids)) == 3

    async def test_all_punctuation_filename(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """全标点文件名 → 全部变 _，仍合法（_ 是 \\w）不触发 'upload' fallback。"""
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id, filename="!!!@@@",
            content="x", content_type="text/plain",
        )
        assert ok
        import re
        assert re.match(r"^[\w\-.]{1,64}$", info["id"])
        assert info["id"] == "______"  # 6 个 _

    async def test_empty_filename_sanitized(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """空文件名 → 走 fallback 'upload'。"""
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id, filename="",
            content="x", content_type="text/plain",
        )
        assert ok
        assert info["id"] == "upload"

    async def test_explicit_artifact_id_used_verbatim(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """显式 artifact_id(persist upsert 新建路径)→ 原样落 id + title,不派生自文件名。"""
        artifact_service.set_session(session_id)
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id, filename="gallery.html",
            content="<html/>", content_type="text/html",
            artifact_id="风格样单",
        )
        assert ok
        assert info["id"] == "风格样单"
        # title 跟随语义 id(而非临时文件名 "gallery"),面板显示模型挑的名字
        assert info["title"] == "风格样单"

    async def test_explicit_invalid_artifact_id_loud_fails(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """显式 id 不合法 → loud-fail(与 create_artifact 一致,不静默 normalize)。"""
        artifact_service.set_session(session_id)
        ok, message, info = await artifact_service.create_from_upload(
            session_id=session_id, filename="x.txt",
            content="x", content_type="text/plain",
            artifact_id="bad id/with space",
        )
        assert not ok
        assert info is None
        assert "Invalid artifact_id" in message

    async def test_chinese_filename_preserved(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """中文文件名：Python 3 默认 \\w 是 Unicode-aware，中文应被保留。

        Regression guard：如果以后有人加了 re.ASCII 或改了正则，中文会
        全部变成 _，让所有中文上传变成相同 ID 互相 dedup 冲突。
        """
        ok, _, info = await artifact_service.create_from_upload(
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
        self, artifact_service: ArtifactService, session_id: str
    ):
        """中文 + 标点符号：中文保留，全角 / 半角标点变 _。"""
        ok, _, info = await artifact_service.create_from_upload(
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
        self, artifact_service: ArtifactService, session_id: str, bad_id: str,
    ):
        artifact_service.set_session(session_id)
        ok, msg = await artifact_service.create_artifact(
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
        self, artifact_service: ArtifactService, session_id: str, good_id: str,
    ):
        artifact_service.set_session(session_id)
        ok, msg = await artifact_service.create_artifact(
            session_id=session_id, artifact_id=good_id,
            content_type="text/plain", title="t", content="x",
        )
        assert ok, msg


class TestWriteBackFlush:
    """Verify that flush_all collapses in-memory edits into a single DB version."""

    async def test_create_then_updates_produce_single_version(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        """create -> update -> update -> flush produces one version record at v3."""
        artifact_service.set_session(session_id)

        # In-memory create (v1)
        ok, _ = await artifact_service.create_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            content_type="text/markdown",
            title="Plan",
            content="# Step 1",
        )
        assert ok

        # In-memory update (v2)
        ok, _, _ = await artifact_service.update_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            old_str="# Step 1",
            new_str="# Step 1\n# Step 2",
        )
        assert ok

        # In-memory update (v3)
        ok, _, _ = await artifact_service.update_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            old_str="# Step 1\n# Step 2",
            new_str="# Step 1\n# Step 2\n# Step 3",
        )
        assert ok

        # Verify memory state
        memory = await artifact_service.get_artifact(session_id, "task_plan")
        assert memory is not None
        assert memory.current_version == 3

        # DB should have nothing yet
        db_art = await artifact_repo.get_artifact(session_id, "task_plan")
        assert db_art is None

        # Flush
        await artifact_service.flush_all(session_id)

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
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
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

        artifact_service.set_session(session_id)

        # Two in-memory updates (v2, v3)
        ok, _, _ = await artifact_service.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="initial",
            new_str="updated once",
        )
        assert ok

        ok, _, _ = await artifact_service.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="updated once",
            new_str="updated twice",
        )
        assert ok

        await artifact_service.flush_all(session_id)

        db_art = await artifact_repo.get_artifact(session_id, "report")
        assert db_art.current_version == 3
        assert db_art.content == "updated twice"

        # Two version records: v1 (original create) + v3 (flushed update)
        # v2 is skipped — sparse version numbers are by design
        versions = await artifact_repo.list_versions(session_id, "report")
        assert len(versions) == 2
        assert [v.version for v in versions] == [1, 3]

    async def test_flush_is_idempotent(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        """Calling flush_all twice does not create duplicate records."""
        artifact_service.set_session(session_id)

        ok, _ = await artifact_service.create_artifact(
            session_id=session_id,
            artifact_id="doc",
            content_type="text/markdown",
            title="Doc",
            content="hello",
        )
        assert ok

        await artifact_service.flush_all(session_id)
        await artifact_service.flush_all(session_id)  # no-op

        versions = await artifact_repo.list_versions(session_id, "doc")
        assert len(versions) == 1


class TestWriteBackInventory:
    """Verify that list_artifacts merges in-memory state during execution."""

    async def test_list_includes_unflushed_new_artifact(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """New in-memory artifact appears in list_artifacts before flush."""
        artifact_service.set_session(session_id)

        ok, _ = await artifact_service.create_artifact(
            session_id=session_id,
            artifact_id="plan",
            content_type="text/markdown",
            title="Plan",
            content="# Plan",
        )
        assert ok

        artifacts = await artifact_service.list_artifacts(session_id)
        assert len(artifacts) == 1
        assert artifacts[0]["id"] == "plan"
        assert artifacts[0]["content"] == "# Plan"

    async def test_list_preserves_insertion_order_for_unflushed(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """In-memory new artifacts must come back in creation order.

        Regression: `_new` used to be a `set()` whose iteration is hash-ordered,
        not insertion-ordered — so session-scope consumers (grep session-mode cap
        truncation, inventory rendering) saw a non-deterministic order across runs.
        """
        artifact_service.set_session(session_id)
        # Use 10 IDs whose hash order is essentially guaranteed to differ
        # from creation order — short alphanumeric strings reshuffle under
        # PYTHONHASHSEED randomization. 10 entries makes accidental same-order
        # vanishingly unlikely.
        ids = [f"art_{i:02d}" for i in range(10)]
        for aid in ids:
            ok, _ = await artifact_service.create_artifact(
                session_id=session_id,
                artifact_id=aid,
                content_type="text/plain",
                title=aid,
                content=f"body of {aid}",
            )
            assert ok

        artifacts = await artifact_service.list_artifacts(session_id)
        assert [a["id"] for a in artifacts] == ids

    async def test_list_returns_stable_order_across_flush(
        self, artifact_service: ArtifactService,
        artifact_repo: ArtifactRepository, session_id: str
    ):
        """flush_all 后下一 turn 读 DB 应得到稳定顺序(可复现,不受 PYTHONHASHSEED 影响)。

        Regression: `_dirty` was a `set()` so flush iterated
        in hash order — INSERTs happened in hash order and `created_at` reflected
        flush sequence. Fixed by making `_dirty` insertion-ordered AND adding
        `Artifact.id` tiebreaker in repo.list_artifacts() (since func.now() on
        SQLite has second-resolution and collides for adjacent INSERTs).

        **Limitation acknowledgement(see repo.list_artifacts docstring):** when
        ids don't sort in creation order, post-flush ordering will be `(created_at,
        id)` rather than strict creation order. This test uses `art_00..art_09`
        which sort identically to creation order — it verifies *stability*, not
        strict creation-order preservation. The intentional design trade-off is
        documented in repo.list_artifacts.
        """
        artifact_service.set_session(session_id)
        ids = [f"art_{i:02d}" for i in range(10)]
        for aid in ids:
            ok, _ = await artifact_service.create_artifact(
                session_id=session_id,
                artifact_id=aid,
                content_type="text/plain",
                title=aid,
                content=f"body of {aid}",
            )
            assert ok

        # Flush to DB — simulates end of turn
        await artifact_service.flush_all(session_id)

        # Read back through a fresh manager — simulates next turn
        fresh = ArtifactService(artifact_repo)
        artifacts = await fresh.list_artifacts(session_id)
        assert [a["id"] for a in artifacts] == ids

    async def test_list_post_flush_id_tiebreaker_known_limitation(
        self, artifact_service: ArtifactService,
        artifact_repo: ArtifactRepository, session_id: str
    ):
        """文档化已知限制:同秒创建 + id 字典序与创建顺序冲突 → 后者赢。

        这不是 bug,是用 `(created_at, id)` 作为排序键的必然结果。本测试以
        executable doc 的形式锁住契约:跨 turn 读出的顺序是 `(created_at, id)`
        排序,而非创建顺序。如果哪天加 `creation_seq` 列改契约,这个测试需要更新。
        """
        artifact_service.set_session(session_id)
        # 故意让创建顺序与 id 字典序相反
        creation_order = ["zebra_doc", "monkey_doc", "apple_doc"]
        for aid in creation_order:
            ok, _ = await artifact_service.create_artifact(
                session_id=session_id,
                artifact_id=aid,
                content_type="text/plain",
                title=aid,
                content="x",
            )
            assert ok

        await artifact_service.flush_all(session_id)

        fresh = ArtifactService(artifact_repo)
        artifacts = await fresh.list_artifacts(session_id)
        observed = [a["id"] for a in artifacts]
        # 同秒 created_at 撞 → id tiebreaker → 字典序
        assert observed == sorted(creation_order)
        # 注意:不等于 creation_order(这正是文档化的限制)
        assert observed != creation_order

    async def test_list_shows_dirty_content_over_db(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
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

        artifact_service.set_session(session_id)

        ok, _, _ = await artifact_service.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="old content",
            new_str="new content",
        )
        assert ok

        artifacts = await artifact_service.list_artifacts(session_id)
        assert len(artifacts) == 1
        assert artifacts[0]["content"] == "new content"
        assert artifacts[0]["version"] == 2


class TestWriteBackFlushFailure:
    """Verify that failed flushes retain dirty state."""

    async def test_failed_flush_keeps_dirty(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        """If flush fails for one artifact, it stays in dirty set."""
        artifact_service.set_session(session_id)

        ok, _ = await artifact_service.create_artifact(
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
            await artifact_service.flush_all(session_id)

        # Dirty entry should still be present
        assert artifact_service.working_set.is_dirty(session_id, "will_fail")


# ============================================================
# blob 类 artifact = 不可变单版：文本编辑工具一律拒绝
# ============================================================

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class TestBinaryArtifactImmutable:
    """update/rewrite 在 blob artifact 上会长出文本 content,与 blob 形成
    双轨 —— service 层统一拒绝;改二进制 = 沙盒 persist 覆盖回写或产新件。"""

    async def _upload_docx(self, artifact_service: ArtifactService, session_id: str) -> str:
        artifact_service.set_session(session_id)
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id,
            filename="spec.docx",
            content="",
            content_type=_DOCX_MIME,
            blob=b"PK\x03\x04" + b"\x00" * 16,
        )
        assert ok
        return info["id"]

    async def test_update_refused(
        self, artifact_service: ArtifactService, session_id: str
    ):
        aid = await self._upload_docx(artifact_service, session_id)
        ok, msg, _ = await artifact_service.update_artifact(
            session_id, aid, old_str="a", new_str="b"
        )
        assert not ok
        assert "no editable text" in msg
        # 拒绝文案指路覆盖回写(mount → 编辑 → persist artifact_id=...)
        assert "persist" in msg

    async def test_rewrite_refused(
        self, artifact_service: ArtifactService, session_id: str
    ):
        aid = await self._upload_docx(artifact_service, session_id)
        ok, msg = await artifact_service.rewrite_artifact(
            session_id, aid, new_content="injected text"
        )
        assert not ok
        assert "no editable text" in msg

    async def test_read_dict_carries_has_blob(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """read_artifact 序列化带 has_blob —— 工具层契约文案 + REST has_blob 的共同
        判别字段(content_type 即原件 MIME,XOR 下无需另存 blob MIME)。"""
        aid = await self._upload_docx(artifact_service, session_id)
        result = await artifact_service.read_artifact(session_id, aid)
        assert result["has_blob"] is True
        assert result["content_type"] == _DOCX_MIME


class TestUploadQuota:
    """per-user blob 配额在写入侧 chokepoint(create_from_upload)守门 —— 覆盖上传 +
    沙盒 persist，计入“DB 已落 + 本轮已 stage 未 flush”的 blob。这里防止沙盒
    persist 再次绕过配额。"""

    async def test_blob_under_quota_succeeds(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        ok, msg, info = await _persist_blob(artifact_service, session_id, "a.bin", 500)
        assert ok, msg
        assert info is not None

    async def test_blob_over_quota_rejected_and_not_staged(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        ok, msg, info = await _persist_blob(artifact_service, session_id, "big.bin", 1500)
        assert not ok
        assert info is None
        assert "quota" in msg.lower()
        # Rejected blob must not have been staged into the WorkingSet.
        staged = artifact_service.working_set.cached(session_id)
        assert all(m.blob is None for m in staged.values())

    async def test_quota_counts_staged_within_turn(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        # Each alone fits under 1000, but 600 + 600 staged this turn exceeds it —
        # the second persist must be rejected (the in-flight staged-bytes subtlety).
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        ok1, _, _ = await _persist_blob(artifact_service, session_id, "f1.bin", 600)
        assert ok1
        ok2, msg2, _ = await _persist_blob(artifact_service, session_id, "f2.bin", 600)
        assert not ok2
        assert "quota" in msg2.lower()

    async def test_quota_counts_committed_after_flush(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        ok1, _, _ = await _persist_blob(artifact_service, session_id, "f1.bin", 600)
        assert ok1
        await artifact_service.flush_all(session_id)
        # committed=600 now; another 600 → 1200 > 1000 → reject.
        ok2, msg2, _ = await _persist_blob(artifact_service, session_id, "f2.bin", 600)
        assert not ok2
        assert "quota" in msg2.lower()

    async def test_flushed_blob_not_double_counted_on_reuse(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        # Regression for the staged/committed double-count: a flushed blob lingers
        # in the WorkingSet cache (clear_one only drops dirty/new marks). If staged
        # scanned the whole cache it would be counted in BOTH committed (DB) and
        # staged → a false rejection on reuse. With quota=1500: committed=600 +
        # staged=0 + incoming=600 = 1200 ≤ 1500 → must be ALLOWED. (Scanning the
        # cache instead of dirty would compute 1800 and wrongly reject.)
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1500)
        ok1, _, _ = await _persist_blob(artifact_service, session_id, "f1.bin", 600)
        assert ok1
        await artifact_service.flush_all(session_id)
        ok2, msg2, _ = await _persist_blob(artifact_service, session_id, "f2.bin", 600)
        assert ok2, msg2

    async def test_quota_counts_across_user_sessions(
        self,
        artifact_service: ArtifactService,
        artifact_repo: ArtifactRepository,
        conversation_repo: ConversationRepository,
        test_user: User,
        session_id: str,
        monkeypatch,
    ):
        # A different conversation of the SAME user already holds 700 committed
        # bytes → persisting 600 more in this session crosses the 1000 quota.
        other = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(conversation_id=other, user_id=test_user.id)
        await artifact_repo.create_artifact(
            session_id=other, artifact_id="prev.bin",
            content_type="application/octet-stream", title="prev", content="",
            blob=b"x" * 700,
        )
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        ok, msg, _ = await _persist_blob(artifact_service, session_id, "f.bin", 600)
        assert not ok
        assert "quota" in msg.lower()


async def _read_blob_row(artifact_repo: ArtifactRepository, session_id: str, aid: str):
    """列级 select 取 (data, size_bytes) —— 绕开 identity map(expire_on_commit=False
    下,bulk-UPDATE 后同 session 的 ORM 实例是 stale 的,直读实例会拿到旧字节)。"""
    result = await artifact_repo._session.execute(
        select(ArtifactBlob.data, ArtifactBlob.size_bytes).where(
            ArtifactBlob.session_id == session_id, ArtifactBlob.artifact_id == aid
        )
    )
    return result.one_or_none()


class TestReplaceFromUpload:
    """persist 覆盖回写:同一 artifact 换内容。文本走 rewrite(版本 +1),blob 走
    可变单版原地替换(不产版本行);种类错配 loud-fail;配额按净增量准入。"""

    async def _upload_text(self, svc, session_id, name="notes.md", body="v1") -> str:
        svc.set_session(session_id)
        ok, _, info = await svc.create_from_upload(
            session_id=session_id, filename=name, content=body,
            content_type="text/markdown", source="sandbox",
        )
        assert ok
        return info["id"]

    async def _upload_blob(self, svc, session_id, name="pkg.zip", data=b"old-bytes") -> str:
        svc.set_session(session_id)
        ok, _, info = await svc.create_from_upload(
            session_id=session_id, filename=name, content="",
            content_type="application/zip", blob=data, source="sandbox",
        )
        assert ok
        return info["id"]

    async def test_text_replace_bumps_version_and_flushes(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        aid = await self._upload_text(artifact_service, session_id)
        await artifact_service.flush_all(session_id)

        ok, msg, info = await artifact_service.replace_from_upload(
            session_id, aid, content="v2 body"
        )
        assert ok, msg
        assert info["current_version"] == 2
        assert info["has_blob"] is False
        await artifact_service.flush_all(session_id)

        row = await artifact_repo.get_artifact(session_id, aid)
        assert row.content == "v2 body"
        assert row.current_version == 2
        assert row.source == "sandbox"
        # 文本历史照常可回溯
        old = await artifact_repo.get_version_content(session_id, aid, 1)
        assert old == "v1"

    async def test_blob_replace_swaps_bytes_no_version_row(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        aid = await self._upload_blob(artifact_service, session_id)
        await artifact_service.flush_all(session_id)

        ok, msg, info = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"new-bytes-longer"
        )
        assert ok, msg
        assert info["has_blob"] is True
        await artifact_service.flush_all(session_id)

        data, size = await _read_blob_row(artifact_repo, session_id, aid)
        assert data == b"new-bytes-longer"
        assert size == len(b"new-bytes-longer")
        # 可变单版:版本号不动、不产新版本行
        row = await artifact_repo.get_artifact(session_id, aid)
        assert row.current_version == 1
        assert await artifact_repo.get_version(session_id, aid, 2) is None

    async def test_binary_over_text_rejected(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """唯一不可覆盖方向:字节无文本表示,不能盖文本 artifact。"""
        text_id = await self._upload_text(artifact_service, session_id)
        ok, msg, _ = await artifact_service.replace_from_upload(
            session_id, text_id, blob=b"binary"
        )
        assert not ok and "text artifact" in msg

    async def test_text_content_over_blob_coerced_to_bytes(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        """target 类型优先:blob 目标 + 恰好 UTF-8 可解码的内容 → 按字节存,
        不拒掉逼模型堆新件(二进制编辑成可解码文本 / 截空文件的场景)。"""
        blob_id = await self._upload_blob(artifact_service, session_id)
        await artifact_service.flush_all(session_id)
        ok, msg, info = await artifact_service.replace_from_upload(
            session_id, blob_id, content="now plain text"
        )
        assert ok, msg
        assert info["has_blob"] is True  # 类型保持,不翻转
        await artifact_service.flush_all(session_id)
        data, _ = await _read_blob_row(artifact_repo, session_id, blob_id)
        assert data == "now plain text".encode("utf-8")

    async def test_replace_unknown_artifact_fails(
        self, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        ok, msg, _ = await artifact_service.replace_from_upload(
            session_id, "ghost", content="x"
        )
        assert not ok and "not found" in msg

    async def test_blob_replace_quota_credits_replaced_bytes(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        """净增量准入:800 已落库,配额 1000,替换成 900 = 净 +100 → 必须放行
        (若按新增全额记账会误拒:800+900>1000)。换成 1100 → 净超 → 拒。"""
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        aid = await self._upload_blob(artifact_service, session_id, data=b"x" * 800)
        await artifact_service.flush_all(session_id)

        ok, msg, _ = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"y" * 900
        )
        assert ok, msg
        await artifact_service.flush_all(session_id)

        ok, msg, _ = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"z" * 1100
        )
        assert not ok
        assert "quota" in msg.lower()

    async def test_replace_two_committed_blobs_same_turn_credits_both(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        """净占用投影回归：credit 覆盖本轮**全部** replace-staged，
        不止当前目标。DB A=600,B=600,配额 1500;A→500 再 B→500(真实终态
        1000)——只按单目标 credit 时 B 的准入算 1200+500-600+500=1600 误拒。"""
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1500)
        aid_a = await self._upload_blob(artifact_service, session_id, name="a.zip", data=b"a" * 600)
        aid_b = await self._upload_blob(artifact_service, session_id, name="b.zip", data=b"b" * 600)
        await artifact_service.flush_all(session_id)

        ok1, msg1, _ = await artifact_service.replace_from_upload(
            session_id, aid_a, blob=b"x" * 500
        )
        assert ok1, msg1
        ok2, msg2, _ = await artifact_service.replace_from_upload(
            session_id, aid_b, blob=b"y" * 500
        )
        assert ok2, msg2
        # 真超限仍拦:再把 A 换成 600 → 投影 500+600=1100... 换成 1100 → 500+1100=1600 > 1500
        ok3, msg3, _ = await artifact_service.replace_from_upload(
            session_id, aid_a, blob=b"z" * 1100
        )
        assert not ok3 and "quota" in msg3.lower()

    async def test_create_after_replace_credits_replaced_bytes(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        """新建路径同样吃 credit:覆盖 A(600→500)后新建 500,投影 1000 ≤ 1100
        须放行(不抵扣 A 旧字节会算 600+500+500=1600 误拒)。"""
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1100)
        aid = await self._upload_blob(artifact_service, session_id, data=b"a" * 600)
        await artifact_service.flush_all(session_id)

        ok, msg, _ = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"x" * 500
        )
        assert ok, msg
        ok2, msg2, _ = await _persist_blob(artifact_service, session_id, "new.bin", 500)
        assert ok2, msg2

    async def test_blob_double_replace_same_turn_excludes_own_staged(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        """同轮二次覆盖:staged 计数须剔除本 artifact 上一次 stage 的字节,
        否则 600(DB)-600(credit)+500(上次staged)+550 会误拒。"""
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        aid = await self._upload_blob(artifact_service, session_id, data=b"x" * 600)
        await artifact_service.flush_all(session_id)

        ok1, msg1, _ = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"y" * 500
        )
        assert ok1, msg1
        ok2, msg2, _ = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"z" * 550
        )
        assert ok2, msg2

    async def test_same_turn_create_then_replace_folds_into_create(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        """同轮"新建 + 覆盖"折叠:flush 走 create 一次写成最终字节。"""
        aid = await self._upload_blob(artifact_service, session_id, data=b"first")
        ok, msg, _ = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"second"
        )
        assert ok, msg
        await artifact_service.flush_all(session_id)

        data, _ = await _read_blob_row(artifact_repo, session_id, aid)
        assert data == b"second"

    async def test_replaced_bytes_visible_to_same_turn_get_blob(
        self, artifact_service: ArtifactService, session_id: str
    ):
        """同轮 mount(get_blob)读到的是 staged 新字节,不是 DB 旧字节。"""
        aid = await self._upload_blob(artifact_service, session_id, data=b"old")
        await artifact_service.flush_all(session_id)
        ok, msg, _ = await artifact_service.replace_from_upload(
            session_id, aid, blob=b"fresh"
        )
        assert ok, msg
        blob_info = await artifact_service.get_blob(session_id, aid)
        assert blob_info["data"] == b"fresh"

    async def test_blob_replace_cross_turn_fresh_service(
        self, artifact_service: ArtifactService, artifact_repo: ArtifactRepository, session_id: str
    ):
        """跨轮覆盖(prod 真实路径):新 turn 的 fresh service 空 WorkingSet,
        从 DB 载入 memory(blob lazy 未载,has_blob 取列值)→ 种类判别与
        原地替换照常工作。"""
        aid = await self._upload_blob(artifact_service, session_id, data=b"turn1")
        await artifact_service.flush_all(session_id)

        fresh = ArtifactService(artifact_repo)
        fresh.set_session(session_id)
        ok, msg, _ = await fresh.replace_from_upload(session_id, aid, blob=b"turn2!")
        assert ok, msg
        # 判别来自 DB 列:文本内容打 blob 目标 → target 类型优先,按字节 coerce
        ok2, msg2, info2 = await fresh.replace_from_upload(session_id, aid, content="txt")
        assert ok2, msg2
        assert info2["has_blob"] is True
        await fresh.flush_all(session_id)
        data, _ = await _read_blob_row(artifact_repo, session_id, aid)
        assert data == b"txt"

    async def test_xor_guard_rejects_both_or_neither(
        self, artifact_service: ArtifactService, session_id: str
    ):
        aid = await self._upload_text(artifact_service, session_id)
        ok, _, _ = await artifact_service.replace_from_upload(
            session_id, aid, content="x", blob=b"y"
        )
        assert not ok
        ok, _, _ = await artifact_service.replace_from_upload(session_id, aid)
        assert not ok

    async def test_text_persist_not_gated(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        # blob=None (text) is never counted — even a 1-byte quota lets it through.
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1)
        ok, msg, _ = await artifact_service.create_from_upload(
            session_id=session_id, filename="note.txt", content="x" * 10000,
            content_type="text/plain", source="sandbox",
        )
        assert ok, msg

    async def test_quota_disabled_when_zero(
        self, artifact_service: ArtifactService, session_id: str, monkeypatch
    ):
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 0)
        ok, msg, _ = await _persist_blob(artifact_service, session_id, "big.bin", 10_000_000)
        assert ok, msg


class TestShortSessionPath:
    """引擎路径的 ArtifactService 持 db_manager(repository=None)，DB 读/写各开短
    retrying session(不绑 turn-long session),WorkingSet 留实例做 turn-live 缓存。

    bound-repo 路径(上面所有用例)覆盖不到这条:它构造 ArtifactService(repo)。这里专测
    db_manager 路径,锁死 create/list/flush 都不依赖 bound repo。
    """

    async def test_create_flush_then_list_from_db(self, db_manager, session_id: str):
        svc = ArtifactService(db_manager=db_manager)
        svc.set_session(session_id)
        ok, _ = await svc.create_artifact(
            session_id, "doc.md", "text/markdown", "Doc", "hello"
        )
        assert ok is True
        await svc.flush_all(session_id)

        # 新实例 = 空 WorkingSet → list 只能来自 DB,证明短 session 读通且 flush 已落库。
        fresh = ArtifactService(db_manager=db_manager)
        arts = await fresh.list_artifacts(session_id)
        assert any(a["id"] == "doc.md" for a in arts)

    async def test_create_duplicate_hits_db_existence_check(self, db_manager, session_id: str):
        # 回归:WorkingSet miss 时 create 走 DB 存在性检查。该检查曾误用 _ensure_repository,
        # 在 repository=None 的引擎路径会 RuntimeError —— 此用例锁死它走短 session。
        svc = ArtifactService(db_manager=db_manager)
        svc.set_session(session_id)
        ok, _ = await svc.create_artifact(session_id, "dup.md", "text/markdown", "D", "x")
        assert ok is True
        await svc.flush_all(session_id)

        fresh = ArtifactService(db_manager=db_manager)  # 空 WorkingSet → 必查 DB
        fresh.set_session(session_id)
        ok2, msg2 = await fresh.create_artifact(session_id, "dup.md", "text/markdown", "D", "y")
        assert ok2 is False
        assert "already exists" in msg2

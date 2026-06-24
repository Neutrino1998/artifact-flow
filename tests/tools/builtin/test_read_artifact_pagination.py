"""
Tests for ReadArtifactTool pagination (offset / limit / hidden char cap).

依赖 conftest.py 的 artifact_repo + test_user，与 test_artifact_writeback.py 同款。
"""

import uuid

import pytest

from config import config
from db.models import User
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from tools.builtin.artifact_service import ArtifactService
from tools.builtin.artifact_ops import ReadArtifactTool


@pytest.fixture
async def session_id(conversation_repo: ConversationRepository, test_user: User) -> str:
    conv_id = f"conv-{uuid.uuid4().hex}"
    await conversation_repo.create_conversation(
        conversation_id=conv_id, user_id=test_user.id
    )
    return conv_id


@pytest.fixture
def artifact_service(artifact_repo: ArtifactRepository) -> ArtifactService:
    return ArtifactService(artifact_repo)


@pytest.fixture
def read_tool(artifact_service: ArtifactService) -> ReadArtifactTool:
    return ReadArtifactTool(artifact_service)


async def _create_artifact(manager: ArtifactService, session_id: str, content: str) -> str:
    """Helper: create artifact, return its id."""
    manager.set_session(session_id)
    aid = f"doc_{uuid.uuid4().hex[:8]}"
    ok, _ = await manager.create_artifact(
        session_id=session_id,
        artifact_id=aid,
        content_type="text/plain",
        title="Test Doc",
        content=content,
    )
    assert ok
    return aid


class TestReadArtifactPagination:

    async def test_read_full_under_cap(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """短 artifact 无参调用 → 返回全文，truncated_by=none, has_more=false。"""
        content = "line_1\nline_2\nline_3\n"
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid)
        assert result.success
        assert 'truncated_by="none"' in result.data
        assert 'has_more="false"' in result.data
        assert 'shown_lines="1-3"' in result.data
        assert 'total_lines="3"' in result.data
        assert "line_1\nline_2\nline_3\n" in result.data

    async def test_read_with_offset_and_limit(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """带 offset+limit 范围读取。"""
        content = "".join(f"line_{i}\n" for i in range(1, 11))
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid, offset=3, limit=4)
        assert result.success
        assert 'shown_lines="3-6"' in result.data
        assert 'total_lines="10"' in result.data
        assert 'truncated_by="line_limit"' in result.data
        assert 'has_more="true"' in result.data
        # body 包含 line_3..line_6
        assert "line_3\n" in result.data
        assert "line_6\n" in result.data
        assert "line_7" not in result.data
        # hint 引导下一段
        assert "offset=7" in result.data

    async def test_read_offset_past_eof(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """offset 超出文件末尾 → 空 body，has_more=false，不报错。"""
        content = "line_1\nline_2\n"
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid, offset=10)
        assert result.success
        assert 'has_more="false"' in result.data
        # shown_lines 省略（None）
        assert 'shown_lines' not in result.data

    async def test_read_offset_zero_clamped(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """offset=0 应 clamp 到 1。"""
        content = "line_1\nline_2\n"
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid, offset=0)
        assert result.success
        assert 'shown_lines="1-2"' in result.data

    async def test_read_truncated_by_char_cap(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """无 limit 时 char_cap 触发截断，hint 给出下次 offset。"""
        # 临时把 cap 调小到 30 chars，文档内容 ~100 chars 应被截断
        monkeypatch.setattr(config, "READ_ARTIFACT_MAX_CHARS", 30)
        content = "".join(f"line_{i:02d}\n" for i in range(1, 11))  # 80 chars
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid)
        assert result.success
        assert 'truncated_by="char_limit"' in result.data
        assert 'has_more="true"' in result.data
        # hint 必须存在
        assert "read_artifact" in result.data
        assert "offset=" in result.data

    async def test_read_nonexistent_artifact(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """不存在的 id → success=False, error 友好提示。"""
        artifact_service.set_session(session_id)
        result = await read_tool(id="nonexistent_id")
        assert not result.success
        assert "not found" in (result.error or "").lower()

    async def test_read_envelope_uses_artifact_slice(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """渲染输出确认是新 envelope 格式。"""
        aid = await _create_artifact(artifact_service, session_id, "hello\n")
        result = await read_tool(id=aid)
        assert result.success
        assert result.data.startswith("<artifact_slice")
        assert result.data.endswith("</artifact_slice>")
        assert "<title>Test Doc</title>" in result.data

    async def test_read_max_result_size_chars_is_inf(self, read_tool: ReadArtifactTool):
        """ReadArtifactTool 必须设 max_result_size_chars=inf 以避免循环落盘。"""
        import math
        assert math.isinf(read_tool.max_result_size_chars)

    async def test_read_body_not_escaped(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """body 不转义 → update_artifact 后续匹配能用 read 出的内容作 old_string。"""
        content = '<script>alert("x")</script>\n& more & content\n'
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid)
        assert result.success
        assert content in result.data
        assert "&lt;" not in result.data
        assert "&amp;" not in result.data

    @staticmethod
    def _extract_hint(rendered: str) -> str:
        """从 envelope 输出里抽取 hint 字符串（避免和 envelope 自身的 attribute 误匹配）。"""
        import re
        m = re.search(r'hint="([^"]+)"', rendered)
        assert m, f"no hint attribute in: {rendered}"
        return m.group(1)

    async def test_continuation_hint_preserves_limit(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        """has_more 续读 hint 必须保留 caller 的 limit，避免下次 silently 切到读到 cap 模式。"""
        content = "".join(f"line_{i}\n" for i in range(1, 21))
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid, offset=1, limit=5)
        assert result.success
        assert 'has_more="true"' in result.data
        hint = self._extract_hint(result.data)
        assert "offset=6" in hint
        assert "limit=5" in hint  # 关键：limit 被透传

    async def test_continuation_hint_preserves_version(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """has_more 续读 hint 必须保留 caller 的 version，避免下次跳到 latest。"""
        # 触发 has_more：cap 调小 + 多行内容
        monkeypatch.setattr(config, "READ_ARTIFACT_MAX_CHARS", 30)
        content = "".join(f"line_{i:02d}\n" for i in range(1, 11))  # 80 chars
        aid = await _create_artifact(artifact_service, session_id, content)
        # 显式 version 读取走 DB 路径，需要先 flush
        await artifact_service.flush_all(session_id)

        result = await read_tool(id=aid, version=1)
        assert result.success
        assert 'has_more="true"' in result.data
        hint = self._extract_hint(result.data)
        assert "version=1" in hint  # version 被透传

    async def test_continuation_hint_default_no_extra_args(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """无 limit / 无 version 时 hint 只带 id + offset，不引入冗余参数。"""
        monkeypatch.setattr(config, "READ_ARTIFACT_MAX_CHARS", 30)
        content = "".join(f"line_{i:02d}\n" for i in range(1, 11))
        aid = await _create_artifact(artifact_service, session_id, content)

        result = await read_tool(id=aid)
        assert result.success
        assert 'has_more="true"' in result.data
        hint = self._extract_hint(result.data)
        assert "offset=" in hint
        assert "limit=" not in hint
        assert "version=" not in hint


# ============================================================
# blob-only artifact(docx/pdf 上传,C-0)→ 契约文案,非空 content
# ============================================================

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class TestReadBinaryBlobArtifact:
    """非图片 blob-only artifact:read 返回契约说明(success=True,避免模型重试),
    不返回空文本切片。"""

    async def test_read_docx_blob_returns_contract_message(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id,
            filename="report.docx",
            content="",
            content_type=_DOCX_MIME,
            blob=b"PK\x03\x04" + b"\x00" * 16,
        )
        assert ok

        result = await read_tool.execute(id=info["id"])
        assert result.success
        assert "binary file" in result.data
        assert "report.docx" in result.data
        # 不能把空 content 当文本切片吐回去
        assert "<artifact_slice" not in result.data


class TestReadArtifactVisionGate:
    """识图分支白名单(VISION_VIEWABLE_MIMES)回归:上传翻转后异型图照收 blob,
    但只有 png/jpeg 进识图;其余 image/* 必须落 blob 契约文案(不进 _read_image
    的"试试看"——动图首帧/多页 tiff 等语义坑),文案给 mount+转 PNG 指引。"""

    async def test_gif_blob_gets_contract_message_not_vision(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id,
            filename="anim.gif",
            content="",
            content_type="image/gif",
            blob=b"GIF89a fake bytes",
        )
        assert ok
        result = await read_tool.execute(id=info["id"])
        assert result.success is True              # 契约回答,非失败(防重试循环)
        assert "metadata" not in result.__dict__ or not (result.metadata or {}).get("image")
        assert "PNG/JPEG" in result.data           # 说清识图白名单
        assert "mount" in result.data              # mount + 转 PNG 指引

    async def test_unknown_binary_blob_message_has_mount_hint(
        self, read_tool: ReadArtifactTool, artifact_service: ArtifactService, session_id: str
    ):
        artifact_service.set_session(session_id)
        ok, _, info = await artifact_service.create_from_upload(
            session_id=session_id,
            filename="data.bin",
            content="",
            content_type="application/octet-stream",
            blob=b"\x00\x01",
        )
        assert ok
        result = await read_tool.execute(id=info["id"])
        assert result.success is True
        assert "mount" in result.data

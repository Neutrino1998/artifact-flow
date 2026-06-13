"""
mount / persist 工具单测 — fake SandboxSession + fake ArtifactService(不碰
docker / DB)。staging 的宿主直写直读、realpath 圈地、文本/二进制二分在此验;
真容器路径归 tests/manual/ 矩阵。
"""

import os
from types import SimpleNamespace

import pytest

from config import config
from tools.base import ToolPermission
from tools.builtin.sandbox_ops import MountArtifactTool, PersistFileTool
from tools.builtin.sandbox_session import SandboxUnavailableError


class FakeStageSession:
    """只提供 mount/persist 依赖的最小面:workspace_dir / ensure_container /
    started / sticky_failure。"""

    def __init__(self, tmp_path, fail_ensure=None, sticky=None):
        self.message_id = "msg-stage"
        self._ws = str(tmp_path / "workspace")
        self._fail_ensure = fail_ensure
        self._sticky = sticky
        self._started = False

    @property
    def workspace_dir(self):
        return self._ws

    @property
    def started(self):
        return self._started

    @property
    def sticky_failure(self):
        return self._sticky

    async def ensure_container(self):
        if self._fail_ensure is not None:
            raise self._fail_ensure
        os.makedirs(self._ws, exist_ok=True)
        self._started = True


class FakeArtifactService:
    def __init__(self):
        self.memories: dict = {}
        self.blobs: dict = {}
        self.create_calls: list = []
        self.session_id = "sess-1"

    @property
    def current_session_id(self):
        return self.session_id

    def add_text(self, artifact_id, content, content_type="text/markdown"):
        self.memories[artifact_id] = SimpleNamespace(
            content=content, content_type=content_type, metadata={}
        )

    def add_blob(self, artifact_id, data, mime):
        self.memories[artifact_id] = SimpleNamespace(
            content="", content_type=mime, metadata={"blob_content_type": mime}
        )
        self.blobs[artifact_id] = {"data": data, "content_type": mime}

    async def get_artifact(self, session_id, artifact_id):
        return self.memories.get(artifact_id)

    async def get_blob(self, session_id, artifact_id):
        return self.blobs.get(artifact_id)

    async def create_from_upload(self, **kwargs):
        self.create_calls.append(kwargs)
        return True, "Created", {"id": kwargs["filename"], "has_blob": kwargs.get("blob") is not None}


@pytest.fixture
def service():
    return FakeArtifactService()


@pytest.fixture
def session(tmp_path):
    return FakeStageSession(tmp_path)


# ============================================================
# mount
# ============================================================


class TestMountTool:

    def test_identity(self, session, service):
        tool = MountArtifactTool(session, service)
        assert tool.name == "mount"
        assert tool.permission == ToolPermission.AUTO
        assert [p.name for p in tool.get_parameters()] == ["artifact_id"]

    async def test_mount_text_artifact_writes_utf8(self, session, service):
        service.add_text("notes.md", "# 标题\nbody")
        result = await MountArtifactTool(session, service)(artifact_id="notes.md")
        assert result.success
        path = os.path.join(session.workspace_dir, "notes.md")
        with open(path, encoding="utf-8") as f:
            assert f.read() == "# 标题\nbody"
        assert "/workspace/notes.md" in result.data
        assert result.metadata["content_type"] == "text/markdown"

    async def test_mount_blob_artifact_writes_original_bytes(self, session, service):
        payload = b"PK\x03\x04binary"
        service.add_blob("report.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        result = await MountArtifactTool(session, service)(artifact_id="report.docx")
        assert result.success
        with open(os.path.join(session.workspace_dir, "report.docx"), "rb") as f:
            assert f.read() == payload
        assert result.metadata["bytes"] == len(payload)

    async def test_mount_triggers_lazy_container(self, session, service):
        """lazy key = 首个沙盒工具调用,mount 也要起容器(可能先 mount 再 bash)。"""
        service.add_text("a.txt", "x")
        assert not session.started
        await MountArtifactTool(session, service)(artifact_id="a.txt")
        assert session.started

    async def test_mount_unknown_artifact_fails(self, session, service):
        result = await MountArtifactTool(session, service)(artifact_id="nope")
        assert not result.success
        assert "not found" in result.error

    async def test_mount_sandbox_unavailable_is_tool_failure(self, tmp_path, service):
        session = FakeStageSession(
            tmp_path, fail_ensure=SandboxUnavailableError("quota exhausted")
        )
        service.add_text("a.txt", "x")
        result = await MountArtifactTool(session, service)(artifact_id="a.txt")
        assert not result.success
        assert "quota" in result.error

    async def test_mount_dotdot_id_rejected(self, session, service):
        """id 模式允许 ".." —— 圈地必须把它挡在 workspace 外。"""
        service.add_text("..", "evil")
        result = await MountArtifactTool(session, service)(artifact_id="..")
        assert not result.success

    async def test_mount_over_planted_symlink_does_not_follow(self, session, service, tmp_path):
        """容器内代码可在工作区植 symlink 指池外;mount 覆写不得跟链写出去。"""
        outside = tmp_path / "outside.txt"
        outside.write_text("untouched")
        await session.ensure_container()
        os.symlink(str(outside), os.path.join(session.workspace_dir, "a.txt"))

        service.add_text("a.txt", "mounted content")
        result = await MountArtifactTool(session, service)(artifact_id="a.txt")
        assert result.success
        assert outside.read_text() == "untouched"  # 池外文件未被改写
        with open(os.path.join(session.workspace_dir, "a.txt")) as f:
            assert f.read() == "mounted content"  # symlink 被换成真文件

    async def test_mount_file_readable_under_restrictive_umask(self, session, service):
        """fchmod 绕 umask:backend umask 077 下文件仍 world-readable(容器 uid 1000
        要读得到),否则落 0600 → mount 报成功、后续 bash permission denied。"""
        old = os.umask(0o077)
        try:
            service.add_text("notes.md", "x")
            result = await MountArtifactTool(session, service)(artifact_id="notes.md")
            assert result.success
            mode = os.stat(os.path.join(session.workspace_dir, "notes.md")).st_mode & 0o777
            assert mode == 0o666
        finally:
            os.umask(old)


# ============================================================
# persist
# ============================================================


def _write_ws(session, rel, data: bytes):
    path = os.path.join(session.workspace_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


class TestPersistTool:

    def test_identity(self, session, service):
        tool = PersistFileTool(session, service)
        assert tool.name == "persist"
        assert tool.permission == ToolPermission.AUTO
        assert [p.name for p in tool.get_parameters()] == ["path"]

    async def test_persist_text_file(self, session, service):
        await session.ensure_container()
        _write_ws(session, "summary.md", "# done\n".encode())
        result = await PersistFileTool(session, service)(path="summary.md")
        assert result.success
        call = service.create_calls[0]
        assert call["content"] == "# done\n"
        assert call["content_type"] == "text/markdown"
        assert call["source"] == "sandbox"
        assert call.get("blob") is None
        assert "editable text artifact" in result.data

    async def test_persist_binary_file(self, session, service):
        await session.ensure_container()
        payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        _write_ws(session, "out/plot.png", payload)
        result = await PersistFileTool(session, service)(path="out/plot.png")
        assert result.success
        call = service.create_calls[0]
        assert call["blob"] == payload
        assert call["blob_content_type"] == "image/png"
        assert call["content"] == ""          # C-0 blob-only 约定
        assert call["content_type"] == "image/png"
        assert call["source"] == "sandbox"
        assert result.metadata["has_blob"] is True

    async def test_persist_absolute_workspace_path_accepted(self, session, service):
        await session.ensure_container()
        _write_ws(session, "a.txt", b"hi")
        result = await PersistFileTool(session, service)(path="/workspace/a.txt")
        assert result.success

    async def test_persist_outside_absolute_path_rejected(self, session, service):
        await session.ensure_container()
        result = await PersistFileTool(session, service)(path="/etc/passwd")
        assert not result.success

    async def test_persist_dotdot_escape_rejected(self, session, service):
        await session.ensure_container()
        result = await PersistFileTool(session, service)(path="../tmp/secret")
        assert not result.success
        assert "escape" in result.error

    async def test_persist_symlink_to_outside_rejected(self, session, service, tmp_path):
        """工作区内 symlink 指池外 → 叶子 O_NOFOLLOW 拒(防宿主文件外流进 artifact)。"""
        await session.ensure_container()
        secret = tmp_path / "host-secret"
        secret.write_text("leak me")
        os.symlink(str(secret), os.path.join(session.workspace_dir, "innocent.txt"))
        result = await PersistFileTool(session, service)(path="innocent.txt")
        assert not result.success

    async def test_persist_parent_dir_symlink_rejected(self, session, service, tmp_path):
        """父目录是 symlink 指池外 → 中间组件 O_DIRECTORY|O_NOFOLLOW 拒(P1 TOCTOU
        修复的核心:单次 O_NOFOLLOW 只保护叶子,逐级 openat 才挡得住换父目录)。"""
        await session.ensure_container()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("host secret")
        os.symlink(str(outside), os.path.join(session.workspace_dir, "d"))
        result = await PersistFileTool(session, service)(path="d/file.txt")
        assert not result.success
        assert "escape" in result.error

    async def test_persist_real_subdir_file_ok(self, session, service):
        """真实子目录(非链)下的文件正常 persist —— 逐级 openat 不误伤合法深路径。"""
        await session.ensure_container()
        _write_ws(session, "out/report.md", b"# ok\n")
        result = await PersistFileTool(session, service)(path="out/report.md")
        assert result.success
        assert service.create_calls[0]["content"] == "# ok\n"

    async def test_persist_reflects_sticky_over_nothing_to_persist(self, tmp_path, service):
        """超额杀后 _container=None(started=False),persist 须复述 sticky 配额失败,
        而非吞成"没用过沙盒"(P3:与 bash/mount sticky 行为一致)。"""
        session = FakeStageSession(
            tmp_path,
            sticky="Sandbox workspace exceeded the 2048MB disk quota and was terminated.",
        )
        # started=False(从未 ensure),但 sticky 已置
        result = await PersistFileTool(session, service)(path="out.txt")
        assert not result.success
        assert "quota" in result.error
        assert "nothing to persist" not in result.error

    async def test_persist_missing_file(self, session, service):
        await session.ensure_container()
        result = await PersistFileTool(session, service)(path="nope.txt")
        assert not result.success
        assert "not found" in result.error

    async def test_persist_directory_suggests_archiving(self, session, service):
        await session.ensure_container()
        os.makedirs(os.path.join(session.workspace_dir, "outdir"))
        result = await PersistFileTool(session, service)(path="outdir")
        assert not result.success
        assert "zip" in result.error

    async def test_persist_oversize_rejected_before_read(self, session, service, monkeypatch):
        monkeypatch.setattr(config, "ARTIFACT_BLOB_MAX_BYTES", 4)
        await session.ensure_container()
        _write_ws(session, "big.bin", b"x" * 100)
        result = await PersistFileTool(session, service)(path="big.bin")
        assert not result.success
        assert "too large" in result.error

    async def test_persist_large_utf8_falls_back_to_blob(self, session, service, monkeypatch):
        """可解码但超文本上限 → 按 blob 存(文本物化成本守门)。"""
        monkeypatch.setattr(config, "SANDBOX_PERSIST_MAX_TEXT_BYTES", 8)
        await session.ensure_container()
        _write_ws(session, "huge.csv", b"a,b,c\n" * 10)
        result = await PersistFileTool(session, service)(path="huge.csv")
        assert result.success
        assert service.create_calls[0]["blob"] is not None

    async def test_persist_before_sandbox_used(self, session, service):
        result = await PersistFileTool(session, service)(path="a.txt")
        assert not result.success
        assert "nothing to persist" in result.error

    async def test_blank_path_rejected(self, session, service):
        await session.ensure_container()
        result = await PersistFileTool(session, service)(path="  ")
        assert not result.success

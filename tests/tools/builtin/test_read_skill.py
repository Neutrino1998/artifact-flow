"""skill 工具单测(C-2 read_skill / D-2 mount_skill,L2/L3)。

read_skill 覆盖:可见 slug → 正文 + 条件化 mount 提示 + activated_skill metadata;
不可见 → 404 风格;无正文 → 错误;空 slug → 错误;契约镜像 read_artifact。
mount_skill 覆盖:可见性闸;无 bundle 报错;剥壳前缀(wrapper / 裸根)进 exec 命令;
坏 bundle;解压失败 / 超额 sticky;成功文案含路径/清单/compatibility/wheel 提示。
真容器解压归 tests/manual/ 矩阵(此处 fake session,exec 打桩)。
"""

import io
import math
import os
import zipfile
from types import SimpleNamespace

import pytest

from core.effective_skillset import EffectiveSkillSet
from reconcile.snapshot import SkillInfo
from tools.base import ToolPermission
from tools.builtin.read_skill import (
    MountSkillTool,
    ReadSkillTool,
    create_skill_tools,
)
from tools.builtin.sandbox_session import SandboxError


class _FakeService:
    def __init__(self, bodies=None, bundles=None):
        self._bodies = bodies or {}
        self._bundles = bundles or {}

    async def get_skill_md(self, slug):
        return self._bodies.get(slug)

    async def get_bundle(self, slug):
        return self._bundles.get(slug)


def _skillset(*slugs, has_bundle=False, compatibility=None):
    visible = {
        s: SkillInfo(slug=s, name=s, description="", visibility="public",
                     default_enabled=True, owner_user_id=None, allowed_tools=[],
                     has_bundle=has_bundle, compatibility=compatibility)
        for s in slugs
    }
    return EffectiveSkillSet(visible=visible, enabled=set(slugs))


def _tool(bodies, *visible):
    return ReadSkillTool(_FakeService(bodies), _skillset(*visible))


def _make_zip(members: dict) -> bytes:
    """{name: text} → zip 字节。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


# ============================================================
# read_skill
# ============================================================


def test_contract_mirrors_read_artifact():
    t = _tool({}, )
    assert t.permission == ToolPermission.AUTO
    assert t.max_result_size_chars == math.inf
    assert t.name == "read_skill"


async def test_visible_no_bundle_says_complete():
    t = _tool({"a": "GUIDANCE BODY"}, "a")   # 默认 has_bundle=False
    res = await t.execute(slug="a")
    assert res.success
    assert "GUIDANCE BODY" in res.data
    assert "complete" in res.data.lower()       # 「这就是完整技能」
    assert "mount_skill" not in res.data        # 无 bundle 不指向 mount
    assert res.metadata["activated_skill"] == "a"


async def test_visible_with_bundle_points_to_mount():
    svc = _FakeService({"a": "GUIDANCE BODY"})
    t = ReadSkillTool(svc, _skillset("a", has_bundle=True))
    res = await t.execute(slug="a")
    assert res.success
    assert "mount_skill" in res.data            # 有 bundle → 指向 mount_skill
    assert res.metadata["activated_skill"] == "a"


async def test_invisible_is_not_found():
    t = _tool({"secret": "x"}, "a")   # secret 存在但不在该用户 visible 集
    res = await t.execute(slug="secret")
    assert not res.success
    assert "not found" in res.error.lower()
    assert res.metadata.get("activated_skill") is None


async def test_visible_but_no_content_errors():
    t = _tool({}, "a")    # a 可见但 service 取不到正文
    res = await t.execute(slug="a")
    assert not res.success
    assert "no content" in res.error.lower()


async def test_empty_slug_errors():
    t = _tool({"a": "x"}, "a")
    res = await t.execute(slug="  ")
    assert not res.success
    assert "slug" in res.error.lower()


def test_create_skill_tools_empty_when_no_visible():
    assert create_skill_tools(_FakeService(), None) == []
    assert create_skill_tools(_FakeService(), EffectiveSkillSet()) == []


def test_create_skill_tools_read_only_without_session():
    tools = create_skill_tools(_FakeService(), _skillset("a", has_bundle=True))
    assert [t.name for t in tools] == ["read_skill"]


def test_create_skill_tools_no_mount_when_no_bundled_skill():
    # 有沙盒但全是 prose skill(无 bundle)→ mount_skill 没东西可挂,不建。
    tools = create_skill_tools(
        _FakeService(), _skillset("a", has_bundle=False), sandbox_session=_FakeSandbox()
    )
    assert [t.name for t in tools] == ["read_skill"]


def test_create_skill_tools_builds_mount_with_session_and_bundle():
    tools = create_skill_tools(
        _FakeService(), _skillset("a", has_bundle=True), sandbox_session=_FakeSandbox()
    )
    assert [t.name for t in tools] == ["read_skill", "mount_skill"]


# ============================================================
# mount_skill
# ============================================================


class _ExecResult:
    def __init__(self, exit_code, output):
        self.exit_code = exit_code
        self.output = output
        self.truncated = False
        self.duration = 0.0


class _FakeSandbox:
    """mount_skill 依赖的最小面:tmp_dir(真目录,供 write_file)+ ensure/exec 打桩。"""

    def __init__(self, tmp_dir=None, exec_result=None, exec_error=None,
                 ensure_error=None, sticky=None):
        self.message_id = "msg-mnt"
        self.tmp_dir = tmp_dir
        self._exec_result = exec_result or _ExecResult(0, "SKILL.md\nreferences/\n")
        self._exec_error = exec_error
        self._ensure_error = ensure_error
        self.sticky_failure = sticky
        self.last_command = None

    async def ensure_container(self):
        if self._ensure_error is not None:
            raise self._ensure_error
        if self.tmp_dir is not None:
            os.makedirs(self.tmp_dir, exist_ok=True)

    async def exec(self, command):
        self.last_command = command
        if self._exec_error is not None:
            raise self._exec_error
        return self._exec_result


def _mount_tool(tmp_path, slug="doc", bundle=None, **sandbox_kw):
    bundles = {slug: bundle} if bundle is not None else {}
    svc = _FakeService(bundles=bundles)
    sandbox = _FakeSandbox(tmp_dir=str(tmp_path / "tmp"), **sandbox_kw)
    skillset = _skillset(slug, has_bundle=bundle is not None)
    return MountSkillTool(sandbox, svc, skillset), sandbox


def test_mount_identity():
    tool = MountSkillTool(_FakeSandbox(), _FakeService(), _skillset("a"))
    assert tool.name == "mount_skill"
    assert tool.permission == ToolPermission.AUTO
    assert [p.name for p in tool.get_parameters()] == ["slug"]


async def test_mount_empty_slug(tmp_path):
    tool, _ = _mount_tool(tmp_path)
    res = await tool.execute(slug="  ")
    assert not res.success and "slug" in res.error.lower()


async def test_mount_invisible_not_found(tmp_path):
    tool, _ = _mount_tool(tmp_path, bundle=_make_zip({"SKILL.md": "x"}))
    res = await tool.execute(slug="other")
    assert not res.success and "not found" in res.error.lower()


async def test_mount_no_bundle_errors(tmp_path):
    # 可见但 get_bundle 返回 None(单文件 skill)
    svc = _FakeService(bundles={})
    tool = MountSkillTool(_FakeSandbox(tmp_dir=str(tmp_path / "tmp")), svc, _skillset("doc"))
    res = await tool.execute(slug="doc")
    assert not res.success
    assert "no bundle" in res.error.lower()
    assert "read_skill" in res.error


async def test_mount_bad_bundle_reports_unreadable(tmp_path):
    tool, _ = _mount_tool(tmp_path, bundle=b"not a zip")
    res = await tool.execute(slug="doc")
    assert not res.success
    assert "could not be read" in res.error.lower()


async def test_mount_wrapper_prefix_stripped_in_command(tmp_path):
    bundle = _make_zip({"pkg/SKILL.md": "guide", "pkg/references/n.md": "n"})
    tool, sandbox = _mount_tool(tmp_path, bundle=bundle)
    res = await tool.execute(slug="doc")
    assert res.success, res.error
    # 剥壳:解到 /workspace/.skills/.extract(与 target 同盘)→ mv .extract/pkg → target
    assert "/workspace/.skills/.extract/pkg" in sandbox.last_command
    assert "/workspace/.skills/doc" in sandbox.last_command
    assert "python3 -m zipfile -e" in sandbox.last_command
    # bundle 已 staging 落 tmp_dir
    assert os.path.exists(os.path.join(sandbox.tmp_dir, ".skill-bundle.zip"))


async def test_mount_bare_root_no_prefix(tmp_path):
    bundle = _make_zip({"SKILL.md": "guide", "references/n.md": "n"})
    tool, sandbox = _mount_tool(tmp_path, bundle=bundle)
    res = await tool.execute(slug="doc")
    assert res.success, res.error
    # 裸根:mv .extract(整棵)→ target,无子目录后缀;同盘 rename、无 /tmp 跨挂载
    assert "mv /workspace/.skills/.extract /workspace/.skills/doc" in sandbox.last_command


async def test_mount_success_message_has_path_listing_and_hints(tmp_path):
    bundle = _make_zip({"pkg/SKILL.md": "g", "pkg/wheels/x.whl": "w"})
    svc = _FakeService(bundles={"doc": bundle})
    # 解压阶段的 stderr 告警在哨兵前 → 应被丢弃,不进清单(#3)
    sandbox = _FakeSandbox(
        tmp_dir=str(tmp_path / "tmp"),
        exec_result=_ExecResult(
            0, "WARNING: noise from extraction\n___MOUNT_SKILL_LISTING___\nSKILL.md\nwheels/\n"
        ),
    )
    skillset = _skillset("doc", has_bundle=True, compatibility={"python": ">=3.11"})
    tool = MountSkillTool(sandbox, svc, skillset)
    res = await tool.execute(slug="doc")
    assert res.success
    assert res.metadata["path"] == "/workspace/.skills/doc"
    assert "/workspace/.skills/doc/" in res.data
    assert "wheels/" in res.data                       # 顶层清单透出
    assert "WARNING: noise" not in res.data            # 哨兵前噪音被隔离(#3)
    assert "python" in res.data                        # compatibility 原样
    assert "--no-index" in res.data                    # 离线装「例如」
    assert "/workspace/.skills/doc/wheels" in res.data # wheel 路径示例


async def test_mount_extraction_failure_loud(tmp_path):
    bundle = _make_zip({"SKILL.md": "g"})
    svc = _FakeService(bundles={"doc": bundle})
    sandbox = _FakeSandbox(
        tmp_dir=str(tmp_path / "tmp"),
        exec_result=_ExecResult(1, "boom: bad zip"),
    )
    tool = MountSkillTool(sandbox, svc, _skillset("doc", has_bundle=True))
    res = await tool.execute(slug="doc")
    assert not res.success
    assert "failed to unpack" in res.error.lower()


async def test_mount_over_quota_reports_sticky(tmp_path):
    bundle = _make_zip({"SKILL.md": "g"})
    svc = _FakeService(bundles={"doc": bundle})
    sandbox = _FakeSandbox(
        tmp_dir=str(tmp_path / "tmp"),
        exec_result=_ExecResult(137, ""),
        sticky="Sandbox workspace exceeded the disk quota and was terminated.",
    )
    tool = MountSkillTool(sandbox, svc, _skillset("doc", has_bundle=True))
    res = await tool.execute(slug="doc")
    assert not res.success
    assert "quota" in res.error.lower()               # 归因 sticky 而非裸 137


async def test_mount_ensure_container_failure(tmp_path):
    bundle = _make_zip({"SKILL.md": "g"})
    svc = _FakeService(bundles={"doc": bundle})
    sandbox = _FakeSandbox(
        tmp_dir=str(tmp_path / "tmp"),
        ensure_error=SandboxError("sandbox unavailable this turn"),
    )
    tool = MountSkillTool(sandbox, svc, _skillset("doc", has_bundle=True))
    res = await tool.execute(slug="doc")
    assert not res.success
    assert "unavailable" in res.error.lower()

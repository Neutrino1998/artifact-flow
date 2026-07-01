"""skill 工具(L2 read_skill + L3 mount_skill,Phase C-2 / D-2)。

**read_skill(L2)** 镜像 read_artifact 的**工具契约**(句柄进 / 内容出、
`max_result_size_chars=inf` 永不二次 persist、`AUTO`),但可见性**不照抄 owner-only**
—— skill 多 department 轴,走 EffectiveSkillSet(否则 dept skill 注入挡得住、read
挡不住,changelog 06-23)。激活语义(决策 11/原则 8):read_skill 既返回正文,又声明式
回填 `metadata.activated_skill` —— 引擎据此把 slug 进 `active_skills` + 在已算好的
EffectiveToolset 上 merge 预烤 skill_grants(纯字典、本回合即生效)。工具保持哑、不持
引擎态(对齐 ToolResult.artifact)。

**mount_skill(L3,D-2)** 与 read_skill↔read_artifact 同理拆开 —— 身份空间不同
(user-scoped slug vs session-scoped artifact id)、行为不同(zip 树解压 vs 单文件写),
`mount` 的单 `artifact_id` 参保持不摊(Minimize-parameter-surface)。同一可见性闸,
取 bundle 字节 → 有界拷进容器 /tmp → **在沙盒内**工具驱动 `python -m zipfile -e` 解到
`/workspace/.skills/<slug>/`(解压这个有风险动作圈进 `--network=none`+quota 的沙盒、
zip bomb 只炸本轮,合原则 2)→ 返回路径 / 顶层清单 / 依赖提示。剥壳前缀(SKILL.md 父目录)
runtime 重算(utils.skill_zip,同 D-1 定位器,不持久化)。
"""

import asyncio
import io
import math
import shlex
import zipfile
from typing import List, Optional

from core.effective_skillset import EffectiveSkillSet
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from tools.builtin import sandbox_fs
from tools.builtin.sandbox_session import (
    SKILLS_SUBDIR,
    WORKSPACE_MOUNT,
    SandboxError,
    SandboxSession,
)
from tools.builtin.skill_service import SkillService
from utils.logger import get_logger
from utils.skill_zip import SkillZipError, locate_skill_md, strip_prefix

logger = get_logger("ArtifactFlow")

# 有 bundle:read_skill 只返回 SKILL.md,其余文件要 mount 进沙盒才读得到。
_MOUNT_HINT_BUNDLE = (
    "\n\n---\n"
    "Above is this skill's guidance (SKILL.md). It bundles more files "
    "(references/, scripts/, assets/) that are NOT shown here — call mount_skill "
    "to unpack them into the sandbox, then read or run them with bash."
)
# 无 bundle:SKILL.md 就是完整技能,别去 mount(会得到「无 bundle 可挂」)。
_MOUNT_HINT_NO_BUNDLE = (
    "\n\n---\n"
    "Above is this skill's complete guidance (SKILL.md); it has no bundled files."
)

# 容器内暂存/解压位(固定名 —— 引擎单 turn 内工具串行,无并发覆写;下划线/点前缀
# 不与工作区顶层产物撞眼)。宿主侧写进 session.tmp_dir(= 容器 /tmp 的 bind 源)。
_STAGE_ZIP_NAME = ".skill-bundle.zip"          # 宿主 tmp_dir/<此名> → 容器 /tmp/<此名>
_STAGE_ZIP_CONTAINER = f"/tmp/{_STAGE_ZIP_NAME}"
# 解压落点**在 /workspace(与目标同一个 bind mount)**:最后一步 mv 退化成同盘 rename、
# 零拷贝 —— 若解到 /tmp,mv 跨 mount(EXDEV)会 copy+unlink,峰值瞬时翻倍(Z+2X),
# 近配额 bundle 可能被 watchdog 误杀。`.extract` 点前缀、非合法 slug,不与技能目录撞。
_STAGE_EXTRACT_DIR = f"{WORKSPACE_MOUNT}/{SKILLS_SUBDIR}/.extract"
# 成功清单哨兵:ls 输出前 echo 它,解析时只取哨兵后的部分 —— 隔离解压阶段可能的 stderr
# 告警(_drain_exec 按到达序合流 stdout/stderr,告警在哨兵 echo 前到达 → 被丢弃)。
_LISTING_SENTINEL = "___MOUNT_SKILL_LISTING___"


class ReadSkillTool(BaseTool):
    def __init__(self, service: SkillService, skillset: EffectiveSkillSet):
        super().__init__(
            name="read_skill",
            description=(
                "Load a skill's full guidance (its SKILL.md body) by slug. Call this when a "
                "skill listed in <available_skills> fits the current task — it returns the "
                "instructions AND activates the skill for this conversation (any tools the "
                "skill needs become available). The returned guidance is for this conversation; "
                "if it later scrolls out of context, just read it again."
            ),
            permission=ToolPermission.AUTO,
            # inf = 永不落盘(同 read_artifact:自身输出再落盘会成 read→artifact→read 环)
            max_result_size_chars=math.inf,
        )
        self._service = service
        self._skillset = skillset

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="slug",
                type="string",
                description="Skill slug to load (as shown in <available_skills>).",
                required=True,
            )
        ]

    async def execute(self, **params) -> ToolResult:
        slug = (params.get("slug") or "").strip()
        if not slug:
            return ToolResult(success=False, error="read_skill requires a 'slug'.")
        # 可见性闸 = EffectiveSkillSet(含用户关掉但仍 visible 的 → 合法 opt-in)。
        # 不可见 → 404 风格,不泄露存在性(决策:cross-scope 不漏)。
        info = self._skillset.visible.get(slug)
        if info is None:
            return ToolResult(success=False, error=f"Skill '{slug}' not found.")
        body = await self._service.get_skill_md(slug)
        if body is None:
            return ToolResult(success=False, error=f"Skill '{slug}' has no content.")
        # 提示按 has_bundle 条件化(D-2):有 bundle 才指向 mount_skill,无则说这是完整技能。
        hint = _MOUNT_HINT_BUNDLE if info.has_bundle else _MOUNT_HINT_NO_BUNDLE
        return ToolResult(
            success=True,
            data=body + hint,
            metadata={"activated_skill": slug},  # 引擎据此激活(append + merge skill_grants)
        )


class MountSkillTool(BaseTool):
    """把一个 skill 的 bundle 解进沙盒 `/workspace/.skills/<slug>/`(L3,D-2)。

    可见性闸同 read_skill(EffectiveSkillSet、404 不漏);`bundle=NULL`(单 SKILL.md)→
    明确报「无 bundle 可挂」。解压走**沙盒内工具驱动**:后端只做有界字节拷贝(bundle→
    容器 /tmp、无解压放大),`session.exec` 在 `--network=none`+quota 容器里
    `python -m zipfile -e` → zip bomb 只炸本轮沙盒(合原则 2)。剥壳前缀 runtime 重算。
    """

    def __init__(
        self,
        session: SandboxSession,
        service: SkillService,
        skillset: EffectiveSkillSet,
    ):
        super().__init__(
            name="mount_skill",
            description=(
                "Unpack a skill's bundled files into the sandbox at "
                f"{WORKSPACE_MOUNT}/{SKILLS_SUBDIR}/<slug>/, so bash can read its references "
                "and run its scripts. Call this after read_skill tells you the skill has "
                "bundled files. The sandbox has no network; if a script needs Python packages, "
                "install them offline from any bundled wheels. Skills with no bundle need no "
                "mounting — their SKILL.md is the whole skill."
            ),
            permission=ToolPermission.AUTO,
        )
        self._session = session
        self._service = service
        self._skillset = skillset

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="slug",
                type="string",
                description="Skill slug to mount (as shown in <available_skills>).",
                required=True,
            )
        ]

    async def execute(self, **params) -> ToolResult:
        slug = (params.get("slug") or "").strip()
        if not slug:
            return ToolResult(success=False, error="mount_skill requires a 'slug'.")
        # 可见性闸(404 不漏,同 read_skill)。visible 里拿 SkillInfo 顺带取 compatibility。
        info = self._skillset.visible.get(slug)
        if info is None:
            return ToolResult(success=False, error=f"Skill '{slug}' not found.")

        bundle = await self._service.get_bundle(slug)
        if bundle is None:
            # 单文件 skill:无附属可挂。指回 read_skill,别让模型空撞。
            return ToolResult(
                success=False,
                error=(
                    f"Skill '{slug}' has no bundle to mount — its full guidance is already "
                    "in read_skill and there are no extra files."
                ),
            )

        # 剥壳前缀 = bundle 里唯一 SKILL.md 的父目录(namelist 读中央目录、不解压)。
        # 受信 seed / E 已校验的 bundle 此处必唯一;真读不开 = 数据面问题,ops 要看。
        try:
            names = zipfile.ZipFile(io.BytesIO(bundle)).namelist()
            member = locate_skill_md(names, f"skill bundle '{slug}'")
        except (zipfile.BadZipFile, SkillZipError) as e:
            logger.error(
                f"mount_skill: unreadable bundle for '{slug}' "
                f"(msg={self._session.message_id}): {e}"
            )
            return ToolResult(success=False, error=f"Skill '{slug}' bundle could not be read.")
        prefix = strip_prefix(member)

        try:
            await self._session.ensure_container()
        except SandboxError as e:
            return ToolResult(success=False, error=str(e))  # session 已记 ops 日志

        # 有界字节拷贝进容器 /tmp(宿主直写 tmp_dir,O_NOFOLLOW 圈地同 mount)。
        try:
            await asyncio.to_thread(
                sandbox_fs.write_file, self._session.tmp_dir, _STAGE_ZIP_NAME, bundle
            )
        except OSError as e:
            logger.error(
                f"mount_skill: staging write failed for '{slug}' "
                f"(msg={self._session.message_id}): {e}"
            )
            return ToolResult(
                success=False, error=f"Failed to stage skill '{slug}' into the sandbox."
            )

        result = await self._extract(slug, prefix)
        if isinstance(result, ToolResult):   # 失败已成型
            return result

        target = f"{WORKSPACE_MOUNT}/{SKILLS_SUBDIR}/{slug}"
        return ToolResult(
            success=True,
            data=self._render_success(slug, info, target, listing=result),
            metadata={"path": target, "slug": slug},
        )

    async def _extract(self, slug: str, prefix: str):
        """沙盒内解压 + 按剥壳前缀就位 + 列顶层;成功返回 listing 文本,失败返回 ToolResult。

        单条 `set -e` 命令:解压静默、失败即 abort(stdout=报错、exit≠0);成功时哨兵
        后的 `ls -1Ap` 输出即顶层清单。解压落点在 /workspace(与 target 同盘)→ 末步 mv
        是同盘 rename、零拷贝(不跨 /tmp↔/workspace 翻倍占用);解完删暂存 zip 减稳态。
        动态段(slug/prefix)全 shlex.quote —— 沙盒内注入非提权(模型本就有 bash),quote
        是为怪名不炸命令(correctness)。
        """
        skills_root = f"{WORKSPACE_MOUNT}/{SKILLS_SUBDIR}"
        target = f"{skills_root}/{slug}"
        src = _STAGE_EXTRACT_DIR + (f"/{prefix}" if prefix else "")
        command = (
            "set -e; "
            f"rm -rf {shlex.quote(_STAGE_EXTRACT_DIR)}; "
            f"mkdir -p {shlex.quote(_STAGE_EXTRACT_DIR)}; "
            f"python3 -m zipfile -e {shlex.quote(_STAGE_ZIP_CONTAINER)} "
            f"{shlex.quote(_STAGE_EXTRACT_DIR)}/; "
            f"rm -rf {shlex.quote(target)}; "
            f"mv {shlex.quote(src)} {shlex.quote(target)}; "
            f"rm -rf {shlex.quote(_STAGE_EXTRACT_DIR)}; "
            f"rm -f {shlex.quote(_STAGE_ZIP_CONTAINER)}; "
            f"echo {shlex.quote(_LISTING_SENTINEL)}; "
            f"ls -1Ap {shlex.quote(target)}"
        )
        try:
            exec_result = await self._session.exec(command)
        except SandboxError as e:
            return ToolResult(success=False, error=str(e))  # session 已记 ops 日志

        if exec_result.exit_code != 0:
            # watchdog 超额杀(zip bomb)→ sticky 归因;否则受信 bundle 解不开 = 意外,ops 要看。
            sticky = self._session.sticky_failure
            if sticky is not None:
                return ToolResult(success=False, error=sticky)
            logger.error(
                f"mount_skill: extraction failed for '{slug}' "
                f"(exit={exec_result.exit_code}, msg={self._session.message_id}): "
                f"{exec_result.output[:500]}"
            )
            return ToolResult(
                success=False, error=f"Failed to unpack skill '{slug}' into the sandbox."
            )
        # 哨兵后即 ls 清单;哨兵前的一切(解压阶段 stderr 告警)丢弃。缺哨兵(不该发生)
        # 回落整段 output,不静默吞。
        out = exec_result.output
        if _LISTING_SENTINEL in out:
            return out.split(_LISTING_SENTINEL, 1)[1].strip()
        return out.strip()

    def _render_success(self, slug: str, info, target: str, *, listing: str) -> str:
        """成功文案:路径 + 顶层清单 + compatibility 原样 + 离线装依赖作「例如」。
        `info` 由 execute() 传入(已校验非 None),不再二次 lookup。"""
        lines = [f"Mounted skill '{slug}' at {target}/."]
        if listing:
            lines.append("Top-level contents:")
            lines.extend(f"  {ln}" for ln in listing.splitlines())
        if info.compatibility:
            lines.append(f"Declared compatibility: {info.compatibility}")
        # 依赖提示作「例如」—— asset 不假设是 pip 包(可能是 xsd/模板/数据/字体/node),
        # 清单 + SKILL.md 驱动用法,pip 只点破气隙坑这一例(原则 8)。
        lines.append(
            "Read SKILL.md for how to use it. The sandbox has no network — if a script "
            "needs a Python package, install it offline, for example from a bundled "
            f"wheels/ dir: `pip install --no-index --find-links {target}/wheels <pkg>`. "
            "Not every bundled file is a pip package; let SKILL.md and the listing guide you."
        )
        return "\n".join(lines)


def create_skill_tools(
    service: SkillService,
    skillset: Optional[EffectiveSkillSet],
    sandbox_session: Optional[SandboxSession] = None,
) -> List[BaseTool]:
    """请求级 skill 工具工厂(同 create_artifact_tools)。skillset 缺省(无 skill)→ 空集。
    有沙盒 session 时并建 mount_skill(bundle 走沙盒消费)。"""
    if skillset is None or not skillset.visible:
        return []
    tools: List[BaseTool] = [ReadSkillTool(service, skillset)]
    # mount_skill 只在(有沙盒 + 至少一个可见 skill 带 bundle)时才建 —— 全是 prose skill
    # 时它没东西可挂,建了只是给每个 bash agent 加一条死工具行(按需注入,镜像 search_tools)。
    if sandbox_session is not None and any(
        info.has_bundle for info in skillset.visible.values()
    ):
        tools.append(MountSkillTool(sandbox_session, service, skillset))
    return tools

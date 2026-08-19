"""skill 工具：L2 read_skill + L3 mount_skill。

**read_skill(L2)** 镜像 read_artifact 的**工具契约**(句柄进 / 内容出、`AUTO`)。
正文超出通用工具内联阈值时由引擎完整落 artifact；只有最终分页读取器
read_artifact 需要 ``max_result_size_chars=inf``。可见性**不照抄 owner-only**
—— skill 有独立的 department 可见性轴，统一走 EffectiveSkillSet，避免注入路径和读取
路径的权限口径不同。read_skill 既返回正文，又声明式
回填 `metadata.activated_skill` —— 引擎据此把 slug 进 `active_skills` + 在已算好的
EffectiveToolset 上 merge 预烤 skill_grants(纯字典、本回合即生效)。工具保持哑、不持
引擎态；只读取 per-call ToolExecutionContext，为 bundle 指引判断激活后的实际能力
(对齐 ToolResult.artifact)。

**mount_skill(L3)** 与 read_skill↔read_artifact 同理拆开 —— 身份空间不同
(user-scoped slug vs session-scoped artifact id)、行为不同(zip 树解压 vs 单文件写),
`mount` 的单 `artifact_id` 参保持不摊(Minimize-parameter-surface)。同一可见性闸,
取 bundle 字节 → 有界拷进容器 /tmp → **在沙盒内**工具驱动 `python -m zipfile -e` 解到
`/workspace/.skills/<slug>/`（把有风险的解压圈进 `--network=none` + quota 的沙盒，
zip bomb 只影响本轮）→ 返回路径 / 顶层清单 / 依赖提示。剥壳前缀(SKILL.md 父目录)
由 utils.skill_zip 的共享定位器在 runtime 重算，不持久化。
"""

import asyncio
import io
import shlex
import zipfile
from typing import List, Optional

from core.capabilities.effective_skillset import EffectiveSkillSet
from core.capabilities.skill_guidance import can_access_skill_bundle, render_skill_guidance
from tools.base import (
    MOUNT_SKILL_NAME,
    READ_SKILL_NAME,
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolResult,
)
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
    wants_context = True

    def __init__(self, service: SkillService, skillset: EffectiveSkillSet):
        super().__init__(
            name=READ_SKILL_NAME,
            description=(
                "Load a skill's full guidance (its SKILL.md body) by slug. Call this when a "
                "skill listed in <available_skills> fits the current task — it returns the "
                "instructions AND activates the skill for this conversation. Tools become "
                "available only when the skill explicitly grants them and this agent has them "
                "configured as disabled; a skill cannot add tools outside the agent's tool "
                "universe. The returned guidance is for this conversation; "
                "if it later scrolls out of context, just read it again."
            ),
            permission=ToolPermission.AUTO,
        )
        self._service = service
        self._skillset = skillset

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Skill slug to load (as shown in <available_skills>).",
                }
            },
            "required": ["slug"],
            "additionalProperties": False,
        }

    async def execute(
        self, _context: Optional[ToolExecutionContext] = None, **params
    ) -> ToolResult:
        slug = (params.get("slug") or "").strip()
        if not slug:
            return ToolResult(success=False, error="read_skill requires a 'slug'.")
        if _context is None:
            return ToolResult(
                success=False,
                error="read_skill requires engine context but none was injected (engine wiring bug).",
            )
        # 可见性闸 = EffectiveSkillSet(含用户关掉但仍 visible 的 → 合法 opt-in)。
        # 不可见 → 404 风格,不泄露存在性(决策:cross-scope 不漏)。
        info = self._skillset.visible.get(slug)
        if info is None:
            return ToolResult(success=False, error=f"Skill '{slug}' not found.")
        body = await self._service.get_skill_md(info.id)
        if body is None:
            return ToolResult(success=False, error=f"Skill '{slug}' has no content.")
        return ToolResult(
            success=True,
            data=render_skill_guidance(
                body,
                has_extra_files=info.has_extra_files,
                bundle_accessible=can_access_skill_bundle(
                    _context.effective_toolset, slug
                ),
            ),
            metadata={"activated_skill": slug},  # 引擎据此激活(append + merge skill_grants)
        )


class MountSkillTool(BaseTool):
    """把一个 skill 的 bundle 解进沙盒 `/workspace/.skills/<slug>/`（L3）。

    可见性闸同 read_skill(EffectiveSkillSet、404 不漏);无附属文件的单 SKILL.md 技能
    不需要 mount。解压走**沙盒内工具驱动**:后端只做有界字节拷贝(bundle→
    容器 /tmp、无解压放大),`session.exec` 在 `--network=none`+quota 容器里
    `python -m zipfile -e` → zip bomb 只影响本轮沙盒。剥壳前缀在 runtime 重算。
    """

    wants_context = True

    def __init__(
        self,
        session: SandboxSession,
        service: SkillService,
        skillset: EffectiveSkillSet,
    ):
        super().__init__(
            name=MOUNT_SKILL_NAME,
            description=(
                "Unpack a skill's bundled files into the sandbox at "
                f"{WORKSPACE_MOUNT}/{SKILLS_SUBDIR}/<slug>/, so bash can read its references "
                "and run its scripts. Call this after read_skill tells you the skill has "
                "bundled files. The sandbox has no network; if a script needs Python packages, "
                "install them offline from any bundled wheels. Skills with no extra files need "
                "no mounting — their SKILL.md is the whole skill."
            ),
            permission=ToolPermission.AUTO,
        )
        self._session = session
        self._service = service
        self._skillset = skillset

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Skill slug to mount (as shown in <available_skills>).",
                }
            },
            "required": ["slug"],
            "additionalProperties": False,
        }

    async def execute(
        self,
        _context: Optional[ToolExecutionContext] = None,
        **params,
    ) -> ToolResult:
        slug = (params.get("slug") or "").strip()
        if not slug:
            return ToolResult(success=False, error="mount_skill requires a 'slug'.")
        if _context is None:
            logger.error("mount_skill requires engine execution context but none was injected")
            return ToolResult(
                success=False,
                error="mount_skill requires engine execution context but none was injected.",
            )
        try:
            self._session.require_fresh_invocation(
                _context.model_invocation_epoch
            )
        except SandboxError as e:
            return ToolResult(
                success=False,
                error=str(e),
                metadata=e.diagnostics,
            )
        # 可见性闸(404 不漏,同 read_skill)。visible 里拿 SkillInfo 顺带取 compatibility。
        info = self._skillset.visible.get(slug)
        if info is None:
            return ToolResult(success=False, error=f"Skill '{slug}' not found.")
        if not info.has_extra_files:
            return ToolResult(
                success=False,
                error=(
                    f"Skill '{slug}' has no extra bundled files to mount — its full guidance "
                    "is already in read_skill."
                ),
            )

        bundle = await self._service.get_bundle(info.id)
        if bundle is None:
            # 可见快照说可 mount,但包取不到:写入/删除竞态或坏数据,ops 要看。
            logger.error(
                f"mount_skill: visible skill '{slug}' has extra files but no bundle "
                f"(msg={self._session.message_id})"
            )
            return ToolResult(
                success=False, error=f"Skill '{slug}' bundle could not be loaded."
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
            return ToolResult(
                success=False,
                error=str(e),
                metadata=e.diagnostics,
            )  # session 已记 ops 日志

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

        result = await self._extract(
            slug,
            prefix,
            model_invocation_epoch=_context.model_invocation_epoch,
        )
        if isinstance(result, ToolResult):   # 失败已成型
            return result

        target = f"{WORKSPACE_MOUNT}/{SKILLS_SUBDIR}/{slug}"
        return ToolResult(
            success=True,
            data=self._render_success(slug, info, target, listing=result),
            metadata={"path": target, "slug": slug},
        )

    async def _extract(
        self,
        slug: str,
        prefix: str,
        *,
        model_invocation_epoch: int,
    ):
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
            exec_result = await self._session.exec(
                command,
                model_invocation_epoch=model_invocation_epoch,
            )
        except SandboxError as e:
            return ToolResult(
                success=False,
                error=str(e),
                metadata=e.diagnostics,
            )  # session 已记 ops 日志

        if exec_result.exit_code != 0:
            # watchdog 超额杀(zip bomb)→ sticky 归因;否则受信 bundle 解不开 = 意外,ops 要看。
            sticky = self._session.sticky_failure
            if sticky is not None:
                return ToolResult(
                    success=False,
                    error=sticky,
                    metadata=self._session.sticky_diagnostics,
                )
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
        # 清单 + SKILL.md 驱动用法；pip 只点破气隙依赖这一例，避免堆叠场景提示。
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
    有沙盒 session 且存在附属文件时并建 mount_skill(bundle 走沙盒消费)。"""
    if skillset is None or not skillset.visible:
        return []
    tools: List[BaseTool] = [ReadSkillTool(service, skillset)]
    # mount_skill 只在(有沙盒 + 至少一个可见 skill 有附属文件)时才建 —— 全是
    # SKILL.md-only 时它没东西可挂；即使 agent 配置了该 builtin，本轮也应收窄掉。
    if sandbox_session is not None and any(
        info.has_extra_files for info in skillset.visible.values()
    ):
        tools.append(MountSkillTool(sandbox_session, service, skillset))
    return tools

"""SkillManager —— 用户侧 skill 列举/toggle(C-3)+ 导入/导出/删除用例编排(E-2)。

三层中的 Manager:经 EffectiveSkillSet 做可见性闸(visible=正确性,miss→404 不泄露存在性),
序列化成前端列表,个人 toggle 写 user_skill 稀疏覆盖。router 只做 transport(认证/HTTP 映射)。

导入是 `source="dynamic"` 的**第一个写入者**:user 私有(audience="private",owner=本人、
default_enabled=True 即刻进自己 L1)与 admin 共享(audience="marketplace",public、owner=null、
default_enabled=True 默认进全员 L1、个人可关闭)双通道同走 import_zip —— 零漂移。硬门 = E-1 validator
(与 seed 同一道门);「能不能跑」归会话期 checker skill(E-4),导入无 verify/force 交互。

seeded skill 归 config 只读(删除/覆盖一律 400 指回 config);dept 授权 UI 归 G。

事务边界 = 每个 use-case:Manager 持 session、调 stage-only repo 后一次 commit(同
ToolRegistryManager;单写用例无需跨 repo 原子性)。
"""

from dataclasses import asdict
from typing import Dict, List, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from core.department_resolver import load_ancestor_ids
from core.effective_skillset import EffectiveSkillSet, resolve_effective_skillset
from reconcile.seeds import _SKILL_CONSUMED_FM_KEYS  # 与 seed 同一份「已消费键」口径
from reconcile.snapshot import SkillInfo, load_skill_snapshot_with_matches
from repositories.skill_repo import SkillRepository
from repositories.tool_registry_repo import ToolRegistryRepository
from tools.base import resolve_allowed_tool_entry
from utils.frontmatter import normalize_allowed_tools
from utils.logger import get_logger
from utils.skill_validator import (
    Finding,
    derive_import_slug,
    validate_skill_zip,
    validate_slug,
)
from utils.skill_zip import has_extra_files

logger = get_logger("ArtifactFlow")
_ADMIN_SHARED_VISIBILITIES = {"public", "department"}


class SkillManagerError(Exception):
    """skill 管理业务错误基类;status_code 供 router 映射 HTTP。"""
    status_code = 400


class SkillNotFoundError(SkillManagerError):
    status_code = 404


class SkillForbiddenError(SkillManagerError):
    """可见但无权操作(共享 dynamic skill 非本人删除)。共享资源本就可见,无需藏成 404。"""
    status_code = 403


class SkillConflictError(SkillManagerError):
    """slug 已占用。文案保持中性(不区分 seeded/他人 private —— 轻微存在性信号,已接受;
    自动改后缀 = 静默 rename 更坏)。默认消息在此单点,预查与并发 IntegrityError 两个
    raise 点共享,不可漂移。"""
    status_code = 409

    def __init__(self, slug: str):
        super().__init__(f"slug '{slug}' 不可用，请修改技能的 name 后重新打包上传")


class SkillQuotaError(SkillManagerError):
    status_code = 413


class SkillCountLimitError(SkillManagerError):
    """Personal skill collection is at its configured limit."""
    status_code = 409


class SkillInternalError(SkillManagerError):
    status_code = 500


class SkillValidationError(SkillManagerError):
    """硬门拒收(E-1 validator error 级 / slug 派生失败 / 单 zip 超限)。findings 结构化
    透出给 router → 422 detail,前端逐条渲染。"""
    status_code = 422

    def __init__(self, message: str, findings: List[Finding]):
        super().__init__(message)
        self.findings = findings


class SkillManager:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = SkillRepository(session)

    async def _resolve(self, user_id: str) -> Tuple[EffectiveSkillSet, Dict[str, bool]]:
        """解析该用户的 EffectiveSkillSet + user_overrides(列举/toggle 复用,同
        controller_factory._load_skills 的口径 —— 单点可见性,杜绝注入有闸/管理没闸漂移)。"""
        dept_id = await self._repo.user_department_id(user_id)
        ancestors = await load_ancestor_ids(self._session, dept_id)
        snapshot, dept_matched = await load_skill_snapshot_with_matches(
            self._session, ancestors
        )
        overrides = await self._repo.user_overrides(user_id)
        eff = resolve_effective_skillset(user_id, snapshot, overrides, dept_matched)
        return eff, overrides

    @staticmethod
    def _serialize(info: SkillInfo, *, enabled: bool, is_overridden: bool, user_id: str) -> dict:
        return {
            "slug": info.slug,
            "name": info.name,
            "description": info.description,
            "enabled": enabled,                       # 有效态(覆盖后,决定进不进 L1)
            "default_enabled": info.default_enabled,  # 系统默认(区分是否被个人改过)
            "is_overridden": is_overridden,
            "source": info.source,                    # dynamic = UI 导入(前端标 badge/可删)
            "has_extra_files": info.has_extra_files,  # 是否有 SKILL.md 外文件需 mount
            "visibility": info.visibility,
            "is_owner": info.owner_user_id == user_id,
        }

    @staticmethod
    def _serialize_admin_shared(row) -> dict:
        return {
            "slug": row.slug,
            "name": row.name,
            "description": row.description,
            "visibility": row.visibility,
            "default_enabled": row.default_enabled,
            "source": row.source,
            "has_extra_files": row.has_extra_files,
            "can_edit": row.source == "dynamic" and row.owner_user_id is None,
        }

    async def list_for_user(self, user_id: str) -> List[dict]:
        """列出该用户**可见**的 skill + 有效启用态(供设置页;保 snapshot 的 slug 顺序)。"""
        eff, overrides = await self._resolve(user_id)
        return [
            self._serialize(
                info, enabled=slug in eff.enabled, is_overridden=slug in overrides,
                user_id=user_id,
            )
            for slug, info in eff.visible.items()
        ]

    async def list_admin_shared(self) -> List[dict]:
        """Admin catalog of shared skills; not filtered by the admin user's department."""
        return [
            self._serialize_admin_shared(row)
            for row in await self._repo.list_shared_skills()
        ]

    async def update_admin_shared(
        self,
        user_id: str,
        slug: str,
        *,
        visibility: str | None = None,
        default_enabled: bool | None = None,
    ) -> dict:
        """Update dynamic shared skill definition fields.

        Only `visibility` and `default_enabled` are mutable here. Seeded skills remain
        config-owned; private/user-owned skills are outside the shared admin catalog.
        Changing visibility clears department rules so old exception rows cannot flip
        direction from deny to grant or back.
        """
        if visibility is None and default_enabled is None:
            logger.warning(
                "Admin shared skill update rejected (400): slug=%s no mutable fields "
                "provided by user=%s",
                slug, user_id,
            )
            raise SkillManagerError(
                "at least one of 'visibility' or 'default_enabled' must be provided"
            )
        if visibility is not None and visibility not in _ADMIN_SHARED_VISIBILITIES:
            logger.warning(
                "Admin shared skill update rejected (400): slug=%s invalid "
                "visibility=%r by user=%s",
                slug, visibility, user_id,
            )
            raise SkillManagerError(
                f"visibility must be one of {sorted(_ADMIN_SHARED_VISIBILITIES)}"
            )

        row = await self._repo.get_skill_for_update(slug)
        if row is None:
            raise SkillNotFoundError(f"skill '{slug}' not found")

        if row.owner_user_id is not None or row.visibility == "private":
            logger.warning(
                "Admin shared skill update rejected (400): slug=%s is private/user-owned, "
                "user=%s",
                slug, user_id,
            )
            raise SkillManagerError(
                f"skill '{slug}' is user-owned and cannot be edited as a shared skill"
            )
        if row.source == "seeded":
            logger.warning(
                "Admin shared skill update rejected (400): slug=%s is seeded "
                "(config-owned), user=%s",
                slug, user_id,
            )
            raise SkillManagerError(
                f"skill '{slug}' 由 config 种子管理，不能在界面编辑"
                "（改 config/skills/ 后重启生效）"
            )
        if row.source != "dynamic":
            logger.warning(
                "Admin shared skill update rejected (400): slug=%s has unsupported "
                "source=%r, user=%s",
                slug, row.source, user_id,
            )
            raise SkillManagerError(
                f"skill '{slug}' has unsupported source '{row.source}'"
            )

        if visibility is not None and row.visibility != visibility:
            old_visibility = row.visibility
            await self._repo.clear_dept_rules(slug)
            row.visibility = visibility
            logger.info(
                "Skill '%s' visibility changed ('%s' → '%s'); department rules "
                "cleared by user=%s",
                slug, old_visibility, visibility, user_id,
            )
        if default_enabled is not None:
            row.default_enabled = default_enabled

        await self._session.commit()
        logger.info("Admin shared skill updated: slug=%s by user=%s", slug, user_id)
        return self._serialize_admin_shared(row)

    async def set_enabled(self, user_id: str, slug: str, enabled: bool) -> dict:
        """个人 enable/disable(写 user_skill 覆盖)。不可见 → 404(不泄露存在性)。"""
        eff, _ = await self._resolve(user_id)
        info = eff.visible.get(slug)
        if info is None:
            raise SkillNotFoundError(f"skill '{slug}' not found")
        await self._repo.set_user_override(user_id, slug, enabled)
        await self._session.commit()
        return self._serialize(info, enabled=enabled, is_overridden=True, user_id=user_id)

    # ------------------------------------------------------------------
    # E-2:导入 / 导出 / 删除
    # ------------------------------------------------------------------

    async def import_zip(
        self,
        user_id: str,
        blob: bytes,
        filename: str,
        *,
        audience: str,
        visibility: str | None = None,
        default_enabled: bool | None = None,
    ) -> dict:
        """导入一个 skill zip。audience:"private"(user 通道,owner=本人、计配额)/
        "marketplace"(admin 通道,共享、配额豁免)。

        管线:单 zip 字节上限(仅 private —— 信任分层,同 SKILL_BUNDLE_MAX_BYTES 注释)
        → 个人数量闸(仅 private,用户行锁保证并发不穿透)→ 字节配额闸(仅 private)
        → E-1 硬门 → slug 派生+校验 → allowed-tools 存在性 warn → 撞名闸
        → 建行 commit。全部拒收路径 logger.warning 落原因(req-id ↔ 拒因,否则 grep
        只见一条 4xx)。
        """
        where = filename or "upload.zip"
        is_private = audience == "private"
        shared_visibility = visibility or "public"
        if not is_private and shared_visibility not in _ADMIN_SHARED_VISIBILITIES:
            f = Finding(
                "visibility.invalid",
                "error",
                f"visibility must be one of {sorted(_ADMIN_SHARED_VISIBILITIES)}",
            )
            logger.warning(
                "Skill import rejected (422): user=%s %s", user_id, f.message
            )
            raise SkillValidationError(f.message, [f])
        resolved_visibility = "private" if is_private else shared_visibility
        resolved_default_enabled = (
            True if is_private or default_enabled is None else default_enabled
        )

        if audience == "private" and len(blob) > config.SKILL_BUNDLE_MAX_BYTES:
            f = Finding(
                "zip.bundle_too_large", "error",
                f"skill zip is {len(blob) / 1024 / 1024:.1f}MB "
                f"(max {config.SKILL_BUNDLE_MAX_BYTES / 1024 / 1024:.0f}MB)",
            )
            logger.warning("Skill import rejected (422): user=%s %s", user_id, f.message)
            raise SkillValidationError(f.message, [f])

        # -1 = unlimited: skip count and lock entirely. For 0/positive limits, lock
        # the stable user row before COUNT and hold it through INSERT+commit. A plain
        # COUNT→INSERT permits two same-user uploads to both consume the last slot.
        private_count_limit = config.SKILL_USER_MAX_PRIVATE_COUNT
        if is_private and private_count_limit >= 0:
            user_exists = await self._repo.lock_user_for_private_import(user_id)
            if not user_exists:
                # Authenticated call paths guarantee this row. Treat disappearance as
                # a server-side failure, not a user-facing "quota" outcome.
                logger.error(
                    "Skill import failed: authenticated user row missing, user=%s",
                    user_id,
                )
                raise SkillInternalError("无法读取当前用户的技能额度")
            owned_count = await self._repo.count_owned_skills(user_id)
            if owned_count >= private_count_limit:
                logger.warning(
                    "Skill import rejected (409): user=%s private skill limit reached "
                    "count=%d limit=%d",
                    user_id, owned_count, private_count_limit,
                )
                if private_count_limit == 0:
                    raise SkillCountLimitError(
                        "个人技能导入已关闭，请联系管理员将技能发布为共享技能"
                    )
                raise SkillCountLimitError(
                    f"你最多可以保留 {private_count_limit} 个个人技能"
                    f"（当前已有 {owned_count} 个）。请删除一个已有技能，"
                    "或联系管理员将技能发布为共享技能。"
                )

        # 配额:bundle 字节与 artifact blob 共用一个池,口径单点在
        # ConversationManager.get_user_upload_bytes(已含 skill 字节)。软上限,挡量级。
        if audience == "private" and config.ARTIFACT_USER_QUOTA_BYTES > 0:
            from core.conversation_manager import ConversationManager
            from repositories.conversation_repo import ConversationRepository

            used = await ConversationManager(
                ConversationRepository(self._session)
            ).get_user_upload_bytes(user_id)
            if used + len(blob) > config.ARTIFACT_USER_QUOTA_BYTES:
                quota_mb = config.ARTIFACT_USER_QUOTA_BYTES / 1024 / 1024
                logger.warning(
                    "Skill import rejected (413): user=%s quota exceeded — "
                    "used=%d incoming=%d quota=%d",
                    user_id, used, len(blob), config.ARTIFACT_USER_QUOTA_BYTES,
                )
                raise SkillQuotaError(
                    f"存储空间不足：本次导入（{len(blob) / 1024 / 1024:.1f}MB）"
                    f"将超出你的 {quota_mb:.0f}MB 存储配额"
                    f"（当前已用 {used / 1024 / 1024:.1f}MB）。"
                    f"请删除一些对话或已导入的技能以释放空间。"
                )

        result = validate_skill_zip(blob, where=where)
        if not result.ok:
            detail = "; ".join(f"[{f.rule}] {f.message}" for f in result.errors)
            logger.warning("Skill import rejected (422): user=%s %s", user_id, detail)
            raise SkillValidationError(
                f"skill zip failed validation: {detail}", result.findings
            )
        parsed = result.parsed
        findings = list(result.findings)  # 全 warning(ok=True),随成功响应透出

        slug = derive_import_slug(parsed.frontmatter, parsed.prefix, where)
        slug_finding = validate_slug(slug)
        if slug_finding is not None:
            logger.warning(
                "Skill import rejected (422): user=%s %s", user_id, slug_finding.message
            )
            raise SkillValidationError(slug_finding.message, findings + [slug_finding])

        # visibility/default_enabled 由通道决定,frontmatter 里的声明被忽略(validator
        # 保持 audience 无关,这条提示归导入侧)
        ignored = sorted({"visibility", "default_enabled"} & parsed.frontmatter.keys())
        if ignored:
            findings.append(Finding(
                "fm.import_ignored_keys", "warning",
                f"frontmatter keys {ignored} are ignored on import "
                "(visibility is set by the import channel)",
            ))

        # allowed-tools 存在性:与 seed / runtime 同一个 resolver + 同一个 inventory
        # loader(ToolRegistryRepository,B-4 撞名闸同款),解析不到 = warn 不拦
        # (unit 后续可挂 / 可建,决策 11)
        allowed_tools = normalize_allowed_tools(
            parsed.frontmatter.get("allowed-tools"), where
        )
        registry = ToolRegistryRepository(self._session)
        known_units = await registry.existing_unit_names()
        known_fulls = await registry.existing_full_names()
        for entry in allowed_tools:
            if resolve_allowed_tool_entry(entry, known_units, known_fulls) is None:
                findings.append(Finding(
                    "tools.unknown_entry", "warning",
                    f"allowed-tools entry '{entry}' resolves to no known tool unit "
                    "(builtin / external unit / <unit>__<tool>) — kept as-is, "
                    "resolved at runtime",
                ))

        if await self._repo.slug_exists(slug):
            logger.warning(
                "Skill import rejected (409): user=%s slug=%s already taken",
                user_id, slug,
            )
            raise SkillConflictError(slug)

        # 单一派生源:行字段与响应序列化都从这一个 SkillInfo 出,POST 响应与后续
        # GET/list_for_user 不可能各算各的(reviewer:两处派生必漂移)。
        frontmatter = parsed.frontmatter
        meta = {k: v for k, v in frontmatter.items() if k not in _SKILL_CONSUMED_FM_KEYS} or None
        info = SkillInfo(
            slug=slug,
            name=(frontmatter.get("name") or slug),
            description=frontmatter.get("description", ""),
            visibility=resolved_visibility,
            # private:owner-only 可见 ⇒ 直接进自己 L1,免 user_skill 行;
            # marketplace:共享发布时由 admin 决定默认是否进 L1,用户可用 user_skill 覆盖。
            default_enabled=resolved_default_enabled,
            owner_user_id=user_id if is_private else None,
            allowed_tools=allowed_tools,
            has_extra_files=has_extra_files(parsed.names, parsed.md_member),
            compatibility=frontmatter.get("compatibility"),
            source="dynamic",
        )
        self._repo.stage_insert_skill(
            slug=info.slug,
            name=info.name,
            description=info.description,
            visibility=info.visibility,
            default_enabled=info.default_enabled,
            owner_user_id=info.owner_user_id,
            allowed_tools=info.allowed_tools,
            compatibility=info.compatibility,
            meta=meta,
            skill_md=parsed.body,       # 剥 frontmatter 正文,永不改写(原则 3)
            bundle=blob,                # 原始上传字节无条件存 → 导出无损 by construction
            has_extra_files=info.has_extra_files,
            source=info.source,
            seed_hash=None,
        )
        try:
            await self._session.commit()
        except IntegrityError:
            # 并发同 slug 首插竞态:slug_exists 预查与 commit 之间对方先落行
            await self._session.rollback()
            logger.warning(
                "Skill import rejected (409, concurrent): user=%s slug=%s", user_id, slug
            )
            raise SkillConflictError(slug)

        logger.info(
            "Skill imported: slug=%s audience=%s user=%s bytes=%d",
            slug, audience, user_id, len(blob),
        )
        return {
            "status": "imported",
            "skill": self._serialize(
                info, enabled=info.default_enabled, is_overridden=False, user_id=user_id
            ),
            "findings": [asdict(f) for f in findings],
        }

    async def export_bundle(self, user_id: str, slug: str) -> bytes:
        """导出 skill zip。写入侧保证单文件 skill 也有 bundle;这里不重新打包。"""
        eff, _ = await self._resolve(user_id)
        if slug not in eff.visible:
            raise SkillNotFoundError(f"skill '{slug}' not found")
        bundle = await self._repo.get_bundle(slug)
        if bundle is None:
            raise SkillNotFoundError(f"skill '{slug}' not found")
        return bundle

    async def export_admin_shared_bundle(self, slug: str) -> bytes:
        """Admin export for the shared catalog, bypassing the admin user's dept scope."""
        bundle = await self._repo.get_shared_bundle(slug)
        if bundle is None:
            raise SkillNotFoundError(f"shared skill '{slug}' not found")
        return bundle

    async def delete_skill(self, user_id: str, slug: str, *, as_admin: bool = False) -> None:
        """删除 dynamic skill。user 通道:不可见→404、seeded→400(config 所有)、
        可见共享非本人→403、own→删;admin 通道:绕过可见性,任意 dynamic 可删。
        user_skill / dept 规则随 DB FK CASCADE 消失。"""
        if as_admin:
            row = await self._repo.get_skill_row_meta(slug)
            if row is None:
                raise SkillNotFoundError(f"skill '{slug}' not found")
            source, owner = row["source"], row["owner_user_id"]
        else:
            eff, _ = await self._resolve(user_id)
            info = eff.visible.get(slug)
            if info is None:
                raise SkillNotFoundError(f"skill '{slug}' not found")
            source, owner = info.source, info.owner_user_id

        if source == "seeded":
            logger.warning(
                "Skill delete rejected (400): slug=%s is seeded (config-owned), user=%s",
                slug, user_id,
            )
            raise SkillManagerError(
                f"skill '{slug}' 由 config 种子管理，不能在界面删除（改 config/skills/ 后重启生效）"
            )
        if not as_admin and owner != user_id:
            raise SkillForbiddenError(f"skill '{slug}' 不是你导入的技能，无法删除")

        await self._repo.delete_skill(slug)
        await self._session.commit()
        logger.info("Skill deleted: slug=%s by user=%s admin=%s", slug, user_id, as_admin)

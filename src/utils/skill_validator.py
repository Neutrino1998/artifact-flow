"""Skill zip 硬门槛 validator(Phase E,决策 7)—— 确定性、纯代码、阻塞门。

**每次导入都跑**(user 私有上传 / admin 共享上传 / config seed 经 seeds.py 同门),
只查 skill 本身良构;「能不能在本系统跑」归软门槛 verify agent(E-3)。两门皆
best-effort 过滤、非正确性闸 —— 正确性兜底在运行时(沙盒 --network=none +
pip --no-index 响失败)。

**宿主侧只读 namelist + SKILL.md 一个成员**(有界:SKILL_MD_MAX_BYTES),全包解压仍归
沙盒(D-2 姿态,zip bomb 只炸本轮沙盒)。zip 的 member 数/声明解压总量上限在此只是
bomb 预拒,沙盒 watchdog 仍是真兜底。

产出 = 结构化 Finding 列表(rule id 稳定,供 REST 422 detail 与前端渲染;severity
error=拒收、warning=透出不拦)。**绝不改写 body**(原则 3:lint 标记 + 人手改)。

复用 skill_zip.locate_skill_md / strip_prefix —— validator / seed / mount 三处对
「哪个是 SKILL.md、剥壳前缀是什么」永远一致。
"""

import io
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from config import config
from utils.frontmatter import (
    FrontmatterError,
    normalize_allowed_tools,
    parse_frontmatter_text,
)
from utils.skill_zip import SkillZipError, locate_skill_md, strip_prefix

# slug 进 shell 路径(/workspace/.skills/<slug>/,已 shlex.quote 但仍求干净)与
# XML-ish 上下文(<available_skills>);≤64 = DB Skill.slug String(64)。
# 小写起步 → `.skills` 保留名 / `_` 禁用前缀(config inert 约定)结构上不可表达。
SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# 开放标准 6 字段 + 我方消费扩展(visibility/default_enabled 由 seed 消费;导入侧
# visibility 由 API 通道决定、frontmatter 里的被忽略 —— 那条 warning 归导入 Manager,
# validator 保持 audience 无关)。
_KNOWN_FM_KEYS = {
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
    "visibility", "default_enabled",
}
# CC 私有扩展(Non-goals):在场即提示会被忽略。
_CC_EXTENSION_KEYS = {"model", "effort", "context", "paths", "disable-model-invocation"}

_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:", "data:", "#")


@dataclass(frozen=True)
class Finding:
    rule: str        # 稳定 id,如 "zip.invalid" / "md.body_empty"
    severity: str    # "error" | "warning"
    message: str


@dataclass
class ParsedSkillZip:
    """结构可读时的解析产物(供 seed/_skill_seed_from_md 与导入 Manager 消费)。"""
    md_member: str
    prefix: str              # 剥壳前缀(SKILL.md 父目录,裸根 = "")
    frontmatter: dict
    body: str                # 正文(frontmatter 已剥离)
    names: List[str]         # zip 文件成员(不含目录条目)


@dataclass
class ValidationResult:
    findings: List[Finding] = field(default_factory=list)
    parsed: Optional[ParsedSkillZip] = None   # None = 结构性不可读(坏 zip / 无法解析 SKILL.md)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]


def slugify_name(raw: str) -> str:
    """frontmatter name → slug 候选(小写、非法字符折 `-`、收敛、截 64)。
    元数据映射、非 body 改写(原则 3 不碰正文)。"""
    s = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-_")
    return s[:64]


def derive_import_slug(frontmatter: dict, prefix: str, filename: str) -> str:
    """导入侧 slug 派生:frontmatter `name` slug 化 → wrapper 目录名 → 上传文件名 stem。
    (seed 侧 slug = config 文件名,operator 控,不走这里。)"""
    name = frontmatter.get("name")
    if isinstance(name, str) and name.strip():
        candidate = slugify_name(name)
        if candidate:
            return candidate
    if prefix:
        candidate = slugify_name(prefix.rsplit("/", 1)[-1])
        if candidate:
            return candidate
    stem = filename.rsplit("/", 1)[-1]
    stem = stem[:-len(".zip")] if stem.lower().endswith(".zip") else stem
    return slugify_name(stem)


def validate_slug(slug: str) -> Optional[Finding]:
    if SKILL_SLUG_RE.match(slug):
        return None
    return Finding(
        rule="slug.invalid",
        severity="error",
        message=(
            f"slug '{slug}' is invalid: must match {SKILL_SLUG_RE.pattern} "
            "(lowercase letters/digits, '-'/'_', max 64 chars)"
        ),
    )


def validate_skill_zip(blob: bytes, *, where: str) -> ValidationResult:
    """一个 skill zip 的全部确定性检查。error 级 → 调用方拒收(seed 转 SeedError /
    导入转 422);warning 级 → 透出不拦。结构性不可读时 parsed=None、提前返回。"""
    result = ValidationResult()
    _add = result.findings.append

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        _add(Finding("zip.invalid", "error", f"{where} is not a valid zip: {e}"))
        return result

    infos = zf.infolist()
    file_infos = [zi for zi in infos if not zi.filename.endswith("/")]
    names = [zi.filename for zi in file_infos]

    if len(file_infos) > config.SKILL_ZIP_MAX_MEMBERS:
        _add(Finding(
            "zip.too_many_members", "error",
            f"{where} has {len(file_infos)} files (max {config.SKILL_ZIP_MAX_MEMBERS})",
        ))
    declared_total = sum(zi.file_size for zi in infos)
    if declared_total > config.SKILL_ZIP_MAX_UNCOMPRESSED_BYTES:
        _add(Finding(
            "zip.uncompressed_too_large", "error",
            f"{where} declares {declared_total / 1024 / 1024:.0f}MB uncompressed "
            f"(max {config.SKILL_ZIP_MAX_UNCOMPRESSED_BYTES / 1024 / 1024:.0f}MB)",
        ))

    traversal = _traversal_offenders(infos)
    if traversal:
        _add(Finding(
            "zip.path_traversal", "error",
            f"{where} has unsafe entries (absolute path / '..' / symlink): {traversal[:5]}",
        ))

    # ---- 定位唯一 SKILL.md(与 seed/mount 共用定位器) ----
    try:
        md_member = locate_skill_md(names, where)
    except SkillZipError as e:
        _add(Finding("zip.skill_md_count", "error", str(e)))
        return result

    prefix = strip_prefix(md_member)
    if prefix:
        stray = sorted(n for n in names if not n.startswith(prefix + "/"))
        if stray:
            _add(Finding(
                "zip.stray_files", "error",
                f"{where} has files outside the SKILL.md root '{prefix}/' ({stray[:10]}); "
                "pack everything under one top-level dir so nothing is dropped at mount",
            ))

    # ---- 有界读 SKILL.md 成员(bomb-in-member:声明 size 可撒谎,按实际读断) ----
    with zf.open(md_member) as fh:
        md_bytes = fh.read(config.SKILL_MD_MAX_BYTES + 1)
    if len(md_bytes) > config.SKILL_MD_MAX_BYTES:
        _add(Finding(
            "md.member_too_large", "error",
            f"SKILL.md exceeds {config.SKILL_MD_MAX_BYTES / 1024 / 1024:.0f}MB",
        ))
        return result
    try:
        md_text = md_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        _add(Finding("md.not_utf8", "error", f"SKILL.md is not valid UTF-8: {e}"))
        return result

    try:
        frontmatter, body = parse_frontmatter_text(md_text, f"{where}:{md_member}")
    except FrontmatterError as e:
        _add(Finding("md.frontmatter_invalid", "error", str(e)))
        return result

    result.parsed = ParsedSkillZip(
        md_member=md_member, prefix=prefix, frontmatter=frontmatter, body=body, names=names,
    )

    # ---- 正文检查 ----
    if not body.strip():
        # 空正文 = 按钮激活会「授能力、永不注正文」而 read_skill 报错(07-02 联审立项):写侧拒。
        _add(Finding(
            "md.body_empty", "error",
            "SKILL.md body is empty (nothing to inject on activation)",
        ))
    prose_lines, unclosed = _split_fences(body)
    if unclosed:
        _add(Finding(
            "md.unclosed_fence", "error",
            f"SKILL.md has an unclosed code fence (opened with {unclosed!r}) — "
            "likely a truncated or broken file",
        ))
    if len(body) > config.SKILL_MD_LEGIBILITY_WARN_CHARS:
        _add(Finding(
            "md.too_long", "warning",
            f"SKILL.md body is {len(body)} chars "
            f"(> {config.SKILL_MD_LEGIBILITY_WARN_CHARS}); consider moving detail "
            "into references/ for legibility",
        ))

    # ---- 相对链接解析(只扫 fence 外的 prose;引用的 asset/script 必须在包里) ----
    referenced = _resolve_links(prose_lines, prefix, names, where, result.findings)

    # ---- 孤儿成员(warning:asset 合法可被脚本加载,只提示) ----
    orphans = [
        n for n in names
        if n != md_member
        and n not in referenced
        and "wheels/" not in n  # 离线 wheel 目录豁免(按目录约定装,天然不被 md 点名)
    ]
    if orphans:
        _add(Finding(
            "zip.orphan_files", "warning",
            f"{len(orphans)} bundled file(s) are never referenced from SKILL.md "
            f"(e.g. {orphans[:5]}); fine if scripts load them, otherwise dead weight",
        ))

    # ---- frontmatter 字段(决策 9:在场才查、缺失宽容) ----
    _check_frontmatter(frontmatter, prefix, result.findings)

    return result


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _traversal_offenders(infos: List[zipfile.ZipInfo]) -> List[str]:
    bad: List[str] = []
    for zi in infos:
        n = zi.filename
        if n.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", n) or ".." in n.split("/"):
            bad.append(n)
        elif stat.S_ISLNK(zi.external_attr >> 16):
            bad.append(f"{n} (symlink)")
    return bad


def _split_fences(body: str) -> Tuple[List[str], Optional[str]]:
    """有状态扫 fence:返回 (fence 外的 prose 行, 未闭合的开栏标记或 None)。
    ``` 与 ~~~ 各自配对(块内的另一种标记是内容);比裸奇偶计数少误报。"""
    prose: List[str] = []
    open_marker: Optional[str] = None
    for line in body.splitlines():
        stripped = line.lstrip()
        marker = None
        if stripped.startswith("```"):
            marker = "```"
        elif stripped.startswith("~~~"):
            marker = "~~~"
        if open_marker is None:
            if marker is not None and len(line) - len(stripped) <= 3:
                open_marker = marker
            else:
                prose.append(line)
        elif marker == open_marker:
            open_marker = None
    return prose, open_marker


def _resolve_links(
    prose_lines: List[str],
    prefix: str,
    names: List[str],
    where: str,
    findings: List[Finding],
) -> Set[str]:
    """SKILL.md 相对链接 → zip 成员校验。返回被引用到的成员集(孤儿检测复用)。"""
    name_set = set(names)
    referenced: Set[str] = set()
    missing: List[str] = []
    for line in prose_lines:
        for target in _LINK_RE.findall(line):
            if target.startswith(_EXTERNAL_LINK_PREFIXES):
                continue
            path = target.split("#", 1)[0].strip()
            if not path:
                continue
            # SKILL.md 在 prefix 顶层,相对链接以其为基准;normpath 吃 ./ 与内部 ..
            resolved = posixpath.normpath(posixpath.join(prefix, path) if prefix else path)
            if resolved in name_set:
                referenced.add(resolved)
            elif any(n.startswith(resolved + "/") for n in name_set):
                referenced.update(n for n in name_set if n.startswith(resolved + "/"))
            else:
                missing.append(target)
    if missing:
        findings.append(Finding(
            "md.link_unresolved", "error",
            f"SKILL.md links to files missing from {where}: {sorted(set(missing))[:10]}",
        ))
    return referenced


def _check_frontmatter(frontmatter: dict, prefix: str, findings: List[Finding]) -> None:
    _add = findings.append

    name = frontmatter.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        _add(Finding("fm.name_invalid", "error", "frontmatter 'name' must be a non-empty string"))
    desc = frontmatter.get("description")
    if desc is not None and (not isinstance(desc, str) or not desc.strip()):
        _add(Finding(
            "fm.description_invalid", "error",
            "frontmatter 'description' must be a non-empty string",
        ))
    try:
        normalize_allowed_tools(frontmatter.get("allowed-tools"), "frontmatter")
    except FrontmatterError as e:
        _add(Finding("fm.allowed_tools_invalid", "error", str(e)))

    compat = frontmatter.get("compatibility")
    if compat is not None and not isinstance(compat, (str, dict, list)):
        _add(Finding(
            "fm.compatibility_invalid", "warning",
            f"frontmatter 'compatibility' has unexpected type {type(compat).__name__}",
        ))
    license_ = frontmatter.get("license")
    if license_ is not None and not isinstance(license_, str):
        _add(Finding(
            "fm.license_invalid", "warning",
            f"frontmatter 'license' has unexpected type {type(license_).__name__}",
        ))
    metadata = frontmatter.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        _add(Finding(
            "fm.metadata_invalid", "warning",
            f"frontmatter 'metadata' has unexpected type {type(metadata).__name__}",
        ))

    cc_keys = sorted(_CC_EXTENSION_KEYS & frontmatter.keys())
    if cc_keys:
        _add(Finding(
            "fm.cc_extension", "warning",
            f"frontmatter keys {cc_keys} are Claude-Code extensions this system ignores",
        ))
    unknown = sorted(frontmatter.keys() - _KNOWN_FM_KEYS - _CC_EXTENSION_KEYS)
    if unknown:
        _add(Finding(
            "fm.unknown_keys", "warning",
            f"unrecognized frontmatter keys {unknown} (kept in metadata, not consumed)",
        ))

    # name↔wrapper 目录一致性(标准约定 <name>/SKILL.md;wrapper 可选故仅 warning)
    if prefix and isinstance(name, str) and name.strip():
        leaf = prefix.rsplit("/", 1)[-1]
        if slugify_name(leaf) != slugify_name(name):
            _add(Finding(
                "fm.name_dir_mismatch", "warning",
                f"wrapper dir '{leaf}' does not match frontmatter name '{name}'",
            ))

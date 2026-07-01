"""
config 种子解析 —— 把 `config/tools/` 与 `config/agents/` 解析成归一化 seed 记录。

纯文件 IO + 校验,**不碰 DB**(DB upsert 在 reconciler.py)。每条 seed 自带内容哈希
(`seed_hash`),reconciler 据此幂等(hash 同则 skip)。

工具 config 形态(目录即工具集):
  - 扁平 `config/tools/foo.md` = singleton unit(kind=tool,1 个 member,full_name==name)。
  - 目录 `config/tools/<set>/` = toolset unit:`_set.md` 给 unit 级 meta、其余 `*.md` = member,
    loader 据 unit 名加 `<set>__` 前缀产 full_name。
`_`/`.` 前缀的文件/目录跳过(同 loaders:operator 禁用 / macOS 垃圾)。
"""

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

from agents.loader import load_agent
from tools.base import BUILTIN_TOOL_NAMES, is_builtin_name, resolve_allowed_tool_entry
from tools.custom.http_tool import validate_response_extract
from tools.custom.secrets import assert_secret_refs_allowed
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

_VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}
_VALID_PERMISSIONS = {"auto", "confirm"}
_VALID_VISIBILITY = {"public", "department"}  # unit 无 private(决策 1)
_VALID_SKILL_VISIBILITY = {"private", "public", "department"}  # skill 独有 private(决策 1)
# skill frontmatter 里系统单独消费的 key(其余 → meta JSON 杂项列,决策 3)
_SKILL_CONSUMED_FM_KEYS = {
    "name", "description", "allowed-tools", "compatibility", "visibility", "default_enabled",
}


class SeedError(ValueError):
    """种子解析期的 loud-fail(坏 config / 命名违规 / 撞名)。"""


# --------------------------------------------------------------------------
# seed 数据形状
# --------------------------------------------------------------------------


@dataclass
class MemberSeed:
    member_name: str          # 作者裸名
    full_name: str            # 注册/可调名:set=<unit>__<member>;singleton==unit_name
    permission: str           # auto | confirm —— 等级唯一来源(决策 11)
    definition: Dict          # http 配置(endpoint/method/headers/parameters/...)+ description


@dataclass
class ToolUnitSeed:
    name: str
    kind: str                 # tool(singleton) | toolset
    description: str          # set 级描述(singleton == member 描述)
    visibility: str
    defer: bool
    provider: str             # http(B)
    members: List[MemberSeed]
    seed_hash: str = ""


@dataclass
class AgentUnitSeed:
    unit_name: str
    member_state: str         # enabled | disabled


@dataclass
class AgentSeed:
    name: str
    description: str
    model: str
    max_tool_rounds: int
    internal: bool
    role_prompt: str
    builtin_tools: Dict[str, str]      # {builtin名: enabled|disabled}
    units: List[AgentUnitSeed] = field(default_factory=list)
    seed_hash: str = ""


@dataclass
class SkillSeed:
    slug: str                          # = 目录名(natural key / PK)
    name: str                          # frontmatter `name`(缺省回落 slug)
    description: str
    visibility: str                    # public | department(seeded 不可 private)
    default_enabled: bool
    allowed_tools: List[str]           # frontmatter `allowed-tools` 原样条目
    compatibility: Optional[dict]      # 气隙依赖声明(C 存,D/E 消费)
    meta: Optional[dict]               # license/version/未知扩展(系统不单独消费)
    skill_md: str                      # SKILL.md 正文(frontmatter 已剥离)
    bundle: Optional[bytes] = None     # 完整原始 zip(目录有 SKILL.md 以外文件才非 NULL,D-1)
    seed_hash: str = ""


# --------------------------------------------------------------------------
# 共用 helper
# --------------------------------------------------------------------------


def _content_hash(payload) -> str:
    """归一化 JSON → sha256。payload 须只含可 JSON 序列化的原语。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _parse_frontmatter_text(content: str, where: str) -> Tuple[dict, str]:
    """MD 文本 → (frontmatter dict, body)。`where` 仅用于报错定位(文件路径 / zip 成员)。"""
    if not content.startswith("---"):
        raise SeedError(f"MD file must start with YAML frontmatter: {where}")
    try:
        end_idx = content.index("---", 3)
    except ValueError:
        raise SeedError(f"MD file has unterminated YAML frontmatter: {where}")
    frontmatter = yaml.safe_load(content[3:end_idx].strip()) or {}
    body = content[end_idx + 3:].strip()
    return frontmatter, body


def _split_frontmatter(path: str) -> Tuple[dict, str]:
    """读 MD 文件 → (frontmatter dict, body)。与 loaders 的切分一致。"""
    with open(path, "r", encoding="utf-8") as f:
        return _parse_frontmatter_text(f.read(), path)


def _is_config_entry(name: str) -> bool:
    """跳过 `_`(operator 禁用)/ `.`(隐藏/垃圾)前缀。"""
    return not name.startswith(("_", "."))


def _validate_unit_name(name: str, source: str) -> None:
    if not name:
        raise SeedError(f"{source}: tool unit missing 'name'")
    if "__" in name:
        # `<unit>__<tool>` 前缀分隔保留 → unit 名禁含 `__`(决策 11)
        raise SeedError(f"{source}: unit name '{name}' must not contain '__'")


def _build_http_member(frontmatter: dict, body: str, *, unit_name: str,
                       is_singleton: bool, source: str) -> MemberSeed:
    """从一个工具/endpoint 的 frontmatter+body 构建 MemberSeed(http provider)。"""
    member_name = frontmatter.get("name")
    if not member_name:
        raise SeedError(f"{source}: tool/endpoint missing 'name'")
    # member 段允许 `__`(MCP 合法名 `^[a-zA-Z0-9_-]{1,64}$`,决策 11):full_name
    # 解析靠剥已知 unit 前缀、绝不 split `__`,故 `github__foo__bar` 合法。仅 unit
    # 名禁 `__`(前缀分隔保留),那个检查在 _validate_unit_name。

    tool_type = frontmatter.get("type", "http")
    if tool_type != "http":
        raise SeedError(f"{source}: unsupported tool type '{tool_type}'")

    permission = frontmatter.get("permission", "confirm")
    if permission not in _VALID_PERMISSIONS:
        raise SeedError(
            f"{source}: invalid permission '{permission}' (expected auto|confirm)"
        )

    # 参数类型校验(同 loader)
    params = []
    for p in frontmatter.get("parameters", []) or []:
        ptype = p.get("type", "string")
        if ptype not in _VALID_PARAM_TYPES:
            raise SeedError(
                f"{source}: unsupported parameter type '{ptype}' for "
                f"'{p.get('name')}'. Valid: {sorted(_VALID_PARAM_TYPES)}"
            )
        params.append({
            "name": p["name"],
            "type": ptype,
            "description": p.get("description", ""),
            "required": p.get("required", True),
            "default": p.get("default"),
            "enum": p.get("enum"),
        })

    # SSRF-02 load-time 闸门:endpoint/headers 的 {{VAR}} 必须白名单前缀
    assert_secret_refs_allowed(frontmatter.get("endpoint", ""))
    assert_secret_refs_allowed(frontmatter.get("headers", {}) or {})

    # response_extract(JMESPath)语法在 reconcile 期 loud-fail —— typo 不留到首次调用
    try:
        validate_response_extract(frontmatter.get("response_extract"))
    except ValueError as e:
        raise SeedError(f"{source}: {e}") from e

    # 描述 = frontmatter.description + body(同 loader 的扩展说明拼接)
    description = frontmatter.get("description", "")
    if body:
        description = f"{description}\n\n{body}" if description else body

    full_name = member_name if is_singleton else f"{unit_name}__{member_name}"

    # method/timeout 归一化与 dynamic CRUD(tool_registry_manager._build_definition)同口径:
    # 同一工具经 MD vs API 落库的 definition 必须一致,否则 seed_hash / GET 展示漂移
    # (reviewer #7;运行期虽被 HttpTool.__init__ 的 .upper() 兜住,存储形态仍应统一)。
    definition = {
        "description": description,
        "endpoint": frontmatter.get("endpoint", ""),
        "method": (frontmatter.get("method", "GET") or "GET").upper(),
        "headers": frontmatter.get("headers", {}) or {},
        "parameters": params,
        "response_extract": frontmatter.get("response_extract"),
        "timeout": int(frontmatter.get("timeout", 60) or 60),
    }
    return MemberSeed(
        member_name=member_name,
        full_name=full_name,
        permission=permission,
        definition=definition,
    )


def _finalize_unit(seed: ToolUnitSeed) -> ToolUnitSeed:
    """算 seed_hash(覆盖所有会改 DB 行的字段)。"""
    seed.seed_hash = _content_hash({
        "kind": seed.kind,
        "description": seed.description,
        "visibility": seed.visibility,
        "defer": seed.defer,
        "provider": seed.provider,
        "members": sorted(
            (m.member_name, m.full_name, m.permission, m.definition)
            for m in seed.members
        ),
    })
    return seed


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


def parse_tool_seeds(tools_dir: str) -> List[ToolUnitSeed]:
    """解析 config/tools/ → ToolUnitSeed 列表(含 intra-config 撞名校验)。"""
    seeds: List[ToolUnitSeed] = []
    if not os.path.isdir(tools_dir):
        return seeds

    for entry in sorted(os.listdir(tools_dir)):
        if not _is_config_entry(entry):
            continue
        path = os.path.join(tools_dir, entry)

        if os.path.isdir(path):
            seeds.append(_parse_toolset_dir(path, entry))
        elif entry.endswith(".md"):
            seeds.append(_parse_singleton_tool(path))
        # 其余(顶层非 .md 文件)忽略

    _check_tool_collisions(seeds)
    return seeds


def _parse_singleton_tool(path: str) -> ToolUnitSeed:
    frontmatter, body = _split_frontmatter(path)
    unit_name = frontmatter.get("name")
    _validate_unit_name(unit_name, path)
    member = _build_http_member(
        frontmatter, body, unit_name=unit_name, is_singleton=True, source=path
    )
    return _finalize_unit(ToolUnitSeed(
        name=unit_name,
        kind="tool",
        description=member.definition["description"],
        visibility=_read_visibility(frontmatter, path),
        defer=bool(frontmatter.get("defer", False)),
        provider="http",
        members=[member],
    ))


def _parse_toolset_dir(dir_path: str, dir_name: str) -> ToolUnitSeed:
    set_md = os.path.join(dir_path, "_set.md")
    if not os.path.isfile(set_md):
        raise SeedError(f"toolset dir '{dir_name}/' missing _set.md")
    set_fm, set_body = _split_frontmatter(set_md)
    unit_name = set_fm.get("name", dir_name)
    _validate_unit_name(unit_name, set_md)

    description = set_fm.get("description", "")
    if set_body:
        description = f"{description}\n\n{set_body}" if description else set_body

    members: List[MemberSeed] = []
    seen: set = set()
    for entry in sorted(os.listdir(dir_path)):
        if not _is_config_entry(entry) or not entry.endswith(".md"):
            continue
        member_path = os.path.join(dir_path, entry)
        fm, body = _split_frontmatter(member_path)
        member = _build_http_member(
            fm, body, unit_name=unit_name, is_singleton=False, source=member_path
        )
        if member.member_name in seen:
            raise SeedError(
                f"toolset '{unit_name}': duplicate member name '{member.member_name}'"
            )
        seen.add(member.member_name)
        members.append(member)

    if not members:
        raise SeedError(f"toolset '{unit_name}': no members (need ≥1 endpoint .md)")

    return _finalize_unit(ToolUnitSeed(
        name=unit_name,
        kind="toolset",
        description=description,
        visibility=_read_visibility(set_fm, set_md),
        defer=bool(set_fm.get("defer", False)),
        provider="http",
        members=members,
    ))


def _read_visibility(frontmatter: dict, source: str) -> str:
    vis = frontmatter.get("visibility", "public")
    if vis not in _VALID_VISIBILITY:
        raise SeedError(
            f"{source}: invalid visibility '{vis}' (expected public|department)"
        )
    return vis


def _check_tool_collisions(seeds: List[ToolUnitSeed]) -> None:
    """命名不变量(单点强制):unit 名与 full_name 在 `builtin ∪ reserved ∪ external`
    扁平命名空间里全局唯一。

    与 builtin 撞名必须 loud-fail:agent 分流 builtin 优先(见 parse_agent_seeds),
    一个叫 `web_search` 的 unit 会被悄悄遮蔽、且 singleton 同名还会在注册表合并时撞
    full_name。(运行期 build_tool_map 的全局闸是双保险,但坏配置应在 seed 期就停。)
    """
    unit_names: set = set()
    full_names: Dict[str, str] = {}
    for s in seeds:
        if is_builtin_name(s.name):
            raise SeedError(
                f"tool unit name '{s.name}' collides with a builtin/reserved tool name"
            )
        if s.name in unit_names:
            raise SeedError(f"duplicate tool unit name '{s.name}' in config/tools")
        unit_names.add(s.name)
        for m in s.members:
            if is_builtin_name(m.full_name):
                raise SeedError(
                    f"tool full_name '{m.full_name}' (unit '{s.name}') collides with "
                    f"a builtin/reserved tool name"
                )
            if m.full_name in full_names:
                raise SeedError(
                    f"duplicate tool full_name '{m.full_name}' "
                    f"(units '{full_names[m.full_name]}' and '{s.name}')"
                )
            full_names[m.full_name] = s.name


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------


def parse_agent_seeds(
    agents_dir: str,
    *,
    known_unit_names: set,
    known_full_names: Dict[str, str],
) -> List[AgentSeed]:
    """
    解析 config/agents/ → AgentSeed 列表。

    把 agent MD `tools:` 条目按 BUILTIN_TOOL_NAMES 分流(决策 11):
      - builtin 名 → builtin_tools
      - 已注册 unit 名 / `<unit>__<tool>` full_name → agent_units(整 unit)
      - 其余 → loud-fail(未知工具)

    MD 值 = 成员态 `enabled` | `disabled`(决策 11:绑定只声明成员态,**不含等级**
    —— 等级唯一来源是工具定义)。旧字面量 `auto`/`confirm`(等级)在此 loud-fail,
    逼迫显式迁移,避免「写了个等级却被静默忽略」的假配置。

    known_unit_names / known_full_names 来自已 reconcile 的 DB(seeded+dynamic),
    使 seeded agent 能引用任意已存在 unit。
    """
    seeds: List[AgentSeed] = []
    if not os.path.isdir(agents_dir):
        return seeds

    seen_names: set = set()
    for filename in sorted(os.listdir(agents_dir)):
        if not filename.endswith(".md") or filename.startswith("."):
            continue
        path = os.path.join(agents_dir, filename)
        config = load_agent(path)  # 复用现有解析(含 model 必填 loud-fail)

        if config.name in seen_names:
            raise SeedError(
                f"duplicate agent name '{config.name}' in config/agents "
                f"(file {filename})"
            )
        seen_names.add(config.name)

        builtin_tools: Dict[str, str] = {}
        unit_states: Dict[str, str] = {}
        for tool_name, raw_state in config.tools.items():
            state = str(raw_state).strip().lower()
            if state not in ("enabled", "disabled"):
                raise SeedError(
                    f"agent '{config.name}' tool '{tool_name}' has invalid member "
                    f"state '{raw_state}' — must be 'enabled' or 'disabled' "
                    f"(decision 11: bindings carry membership only; tool level is "
                    f"sole-sourced from the tool definition, not the agent MD)"
                )
            if tool_name in BUILTIN_TOOL_NAMES:
                builtin_tools[tool_name] = state
            elif tool_name in known_unit_names:
                unit_states[tool_name] = state
            elif tool_name in known_full_names:
                # 引用 set 成员全名 → 归属整 unit(整 unit grant,决策 11)
                unit_states[known_full_names[tool_name]] = state
            else:
                raise SeedError(
                    f"agent '{config.name}' references unknown tool '{tool_name}' "
                    f"(not a builtin, tool unit, or <unit>__<tool> name)"
                )

        units = [AgentUnitSeed(unit_name=u, member_state=st)
                 for u, st in sorted(unit_states.items())]
        seed = AgentSeed(
            name=config.name,
            description=config.description,
            model=config.model,
            max_tool_rounds=config.max_tool_rounds,
            internal=config.internal,
            role_prompt=config.role_prompt,
            builtin_tools=builtin_tools,
            units=units,
        )
        seed.seed_hash = _content_hash({
            "description": seed.description,
            "model": seed.model,
            "max_tool_rounds": seed.max_tool_rounds,
            "internal": seed.internal,
            "role_prompt": seed.role_prompt,
            "builtin_tools": seed.builtin_tools,
            "units": sorted((u.unit_name, u.member_state) for u in seed.units),
        })
        seeds.append(seed)

    return seeds


# --------------------------------------------------------------------------
# skills(Phase C)
# --------------------------------------------------------------------------


def parse_skill_seeds(
    skills_dir: str,
    *,
    known_unit_names: set,
    known_full_names: Dict[str, str],
) -> List["SkillSeed"]:
    """解析 config/skills/ → SkillSeed 列表。两种形态(D-1):

    - **prose skill = `config/skills/<slug>/` 目录**,仅一个 SKILL.md → `bundle=NULL`。
      目录里出现 SKILL.md 以外的真实文件(references/scripts)→ **loud-fail 指向 zip**
      (防"以为打了 bundle、附属文件却被静默丢")。
    - **bundle skill = `config/skills/<slug>.zip`**,slug = 文件名去 `.zip`。存**原始 zip
      字节**(决策 3 无损),`seed_hash=sha256(字节)`;从 zip 里定位唯一 SKILL.md 解 frontmatter。

    zip 形态对齐社区/Anthropic 分发(claude.ai/API 上传即 zip),我方是"服务端 ingest"场景
    (读进 DB、沙盒经 mount 消费),故不像本地 agent 用 config 目录当运行时。

    `allowed-tools` import 期对全局 ceiling 校验存在性,解析不到 → warn 不 fail(决策 11 line 237)。
    """
    seeds: List[SkillSeed] = []
    if not os.path.isdir(skills_dir):
        return seeds

    for entry in sorted(os.listdir(skills_dir)):
        if not _is_config_entry(entry):
            continue
        path = os.path.join(skills_dir, entry)
        if os.path.isdir(path):
            seeds.append(_parse_skill_dir(path, entry, known_unit_names, known_full_names))
        elif entry.endswith(".zip"):
            slug = entry[: -len(".zip")]
            seeds.append(_parse_skill_zip(path, slug, known_unit_names, known_full_names))
        # 其它顶层散文件(非目录、非 .zip)忽略

    return seeds


def _normalize_allowed_tools(raw, source: str) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    raise SeedError(f"{source}: allowed-tools must be a list or string")


def _skill_seed_from_md(
    slug: str,
    frontmatter: dict,
    body: str,
    *,
    bundle: Optional[bytes],
    where: str,
    known_unit_names: set,
    known_full_names: Dict[str, str],
) -> "SkillSeed":
    """共享:frontmatter + body(+ 可选 bundle 字节)→ 校验 + 组装 SkillSeed。
    prose(bundle=None):seed_hash 走归一化列 payload;bundle:seed_hash=sha256(字节)
    —— 提交的 zip 是稳定字节(任何 OS 同 checkout 同哈希),无跨环境 churn。"""
    name = frontmatter.get("name") or slug
    visibility = frontmatter.get("visibility", "public")
    if visibility not in _VALID_SKILL_VISIBILITY:
        raise SeedError(
            f"{where}: invalid visibility '{visibility}' (expected private|public|department)"
        )
    if visibility == "private":
        # seeded skill 是 shared(owner=null),private = owner-only 自相矛盾
        raise SeedError(f"{where}: seeded skill cannot be 'private' (shared, no owner)")

    allowed_tools = _normalize_allowed_tools(frontmatter.get("allowed-tools"), where)
    for entry in allowed_tools:
        if resolve_allowed_tool_entry(entry, known_unit_names, known_full_names) is None:
            # 决策 11 line 237:校验存在性,解析不到 = warn 不 fail(unit 后续可挂 / 可建)
            logger.warning(
                "skill %r: allowed-tools entry %r resolves to no known tool unit "
                "(builtin / external unit / <unit>__<tool>) — kept as-is, resolved at runtime",
                slug, entry,
            )

    meta = {k: v for k, v in frontmatter.items() if k not in _SKILL_CONSUMED_FM_KEYS} or None

    seed = SkillSeed(
        slug=slug,
        name=name,
        description=frontmatter.get("description", ""),
        visibility=visibility,
        default_enabled=bool(frontmatter.get("default_enabled", True)),
        allowed_tools=allowed_tools,
        compatibility=frontmatter.get("compatibility"),
        meta=meta,
        skill_md=body,
        bundle=bundle,
    )
    if bundle is None:
        seed.seed_hash = _content_hash({
            "name": seed.name,
            "description": seed.description,
            "visibility": seed.visibility,
            "default_enabled": seed.default_enabled,
            "allowed_tools": sorted(seed.allowed_tools),
            "compatibility": seed.compatibility,
            "meta": seed.meta,
            "skill_md": seed.skill_md,
        })
    else:
        seed.seed_hash = hashlib.sha256(bundle).hexdigest()
    return seed


def _parse_skill_dir(
    dir_path: str,
    slug: str,
    known_unit_names: set,
    known_full_names: Dict[str, str],
) -> "SkillSeed":
    """prose skill(单 SKILL.md 目录,bundle=NULL)。附属文件 → loud-fail 指向 zip。"""
    skill_md_path = os.path.join(dir_path, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        raise SeedError(f"skill dir '{slug}/' missing SKILL.md")
    # 目录里除 SKILL.md 外的真实条目(忽略 `_`/`.` 垃圾)→ 应打成 <slug>.zip,别用松散目录
    extras = [e for e in os.listdir(dir_path) if e != "SKILL.md" and _is_config_entry(e)]
    if extras:
        raise SeedError(
            f"skill dir '{slug}/' has files besides SKILL.md ({sorted(extras)}); "
            f"bundle skills must be provided as '{slug}.zip', not an unzipped directory"
        )
    frontmatter, body = _split_frontmatter(skill_md_path)
    return _skill_seed_from_md(
        slug, frontmatter, body, bundle=None, where=skill_md_path,
        known_unit_names=known_unit_names, known_full_names=known_full_names,
    )


def _parse_skill_zip(
    zip_path: str,
    slug: str,
    known_unit_names: set,
    known_full_names: Dict[str, str],
) -> "SkillSeed":
    """bundle skill(`<slug>.zip`)。存原始字节;定位唯一 SKILL.md 解 frontmatter。

    定位规则(裸根 / 单层 wrapper `<name>/SKILL.md` / repo 深层嵌套都吃):zip 里唯一的
    SKILL.md 成员即入口,0 个 / 多个 → loud-fail。剥壳前缀(SKILL.md 的父目录)是 D-2
    mount 时的事(解到 /workspace/.skills/<slug>/),此处不需存。"""
    with open(zip_path, "rb") as f:
        blob = f.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise SeedError(f"skill '{slug}.zip' is not a valid zip: {e}")
    md_members = [
        n for n in zf.namelist()
        if not n.endswith("/") and n.rsplit("/", 1)[-1] == "SKILL.md"
    ]
    if not md_members:
        raise SeedError(f"skill '{slug}.zip' contains no SKILL.md")
    if len(md_members) > 1:
        raise SeedError(
            f"skill '{slug}.zip' contains multiple SKILL.md ({sorted(md_members)}); "
            f"one zip = one skill"
        )
    md_text = zf.read(md_members[0]).decode("utf-8")
    frontmatter, body = _parse_frontmatter_text(md_text, f"{slug}.zip:{md_members[0]}")
    return _skill_seed_from_md(
        slug, frontmatter, body, bundle=blob, where=f"{slug}.zip",
        known_unit_names=known_unit_names, known_full_names=known_full_names,
    )

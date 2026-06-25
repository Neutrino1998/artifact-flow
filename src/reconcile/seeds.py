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
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

from agents.loader import load_agent
from tools.base import BUILTIN_TOOL_NAMES
from tools.custom.secrets import assert_secret_refs_allowed

_VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}
_VALID_PERMISSIONS = {"auto", "confirm"}
_VALID_VISIBILITY = {"public", "department"}  # unit 无 private(决策 1)


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


# --------------------------------------------------------------------------
# 共用 helper
# --------------------------------------------------------------------------


def _content_hash(payload) -> str:
    """归一化 JSON → sha256。payload 须只含可 JSON 序列化的原语。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _split_frontmatter(path: str) -> Tuple[dict, str]:
    """读 MD → (frontmatter dict, body)。与 loaders 的切分一致。"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if not content.startswith("---"):
        raise SeedError(f"MD file must start with YAML frontmatter: {path}")
    end_idx = content.index("---", 3)
    frontmatter = yaml.safe_load(content[3:end_idx].strip()) or {}
    body = content[end_idx + 3:].strip()
    return frontmatter, body


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
    if "__" in member_name:
        raise SeedError(f"{source}: member name '{member_name}' must not contain '__'")

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

    # 描述 = frontmatter.description + body(同 loader 的扩展说明拼接)
    description = frontmatter.get("description", "")
    if body:
        description = f"{description}\n\n{body}" if description else body

    full_name = member_name if is_singleton else f"{unit_name}__{member_name}"

    definition = {
        "description": description,
        "endpoint": frontmatter.get("endpoint", ""),
        "method": frontmatter.get("method", "GET"),
        "headers": frontmatter.get("headers", {}) or {},
        "parameters": params,
        "response_extract": frontmatter.get("response_extract"),
        "timeout": frontmatter.get("timeout", 60),
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
    """unit 名全局唯一 + full_name 全局唯一(DB UQ 兜底,这里给清晰报错)。"""
    unit_names: set = set()
    full_names: Dict[str, str] = {}
    for s in seeds:
        if s.name in unit_names:
            raise SeedError(f"duplicate tool unit name '{s.name}' in config/tools")
        unit_names.add(s.name)
        for m in s.members:
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
      - builtin 名 → builtin_tools(present=enabled;等级唯一来源是工具定义,MD 值丢弃)
      - 已注册 unit 名 / `<unit>__<tool>` full_name → agent_units(整 unit)
      - 其余 → loud-fail(未知工具)

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
        for tool_name in config.tools.keys():
            if tool_name in BUILTIN_TOOL_NAMES:
                builtin_tools[tool_name] = "enabled"
            elif tool_name in known_unit_names:
                unit_states[tool_name] = "enabled"
            elif tool_name in known_full_names:
                # 引用 set 成员全名 → 归属整 unit(整 unit grant,决策 11)
                unit_states[known_full_names[tool_name]] = "enabled"
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

"""
ToolRegistryManager —— external 工具 unit / 成员 / agent 挂载 / 凭证的 CRUD 用例编排(B-4)。

三层中的 Manager:ownership/seeded 只读、撞名 by-construction 闸、序列化(密文掩码)、
凭证加密。router 只做 transport(认证/解析/HTTP 映射),不碰这里的业务规则。

事务边界 = 每个 use-case:Manager 持 session、调 stage-only repo 后一次 commit
(tool-registry 写是独立 operator 动作,无需跨 use-case 原子性)。

**撞名主闸在写入期**(`full_name ∉ builtin∪reserved∪已存 external`)—— snapshot 读侧
的 skip+log 只兜绕过写校验的脏行;DB `uq_tool_members_full_name` 是并发 TOCTOU 的硬底。
"""

from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AgentUnit, ToolMember, ToolUnit
from repositories.tool_credential_repo import ToolCredentialRepository
from repositories.tool_registry_repo import ToolRegistryRepository
from tools.base import is_builtin_name
from tools.custom.credentials import get_cipher
from tools.custom.http_tool import validate_response_extract
from tools.custom.secrets import assert_secret_refs_allowed, extract_placeholders, SecretResolutionError
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

_VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}
_VALID_PERMISSIONS = {"auto", "confirm"}
_VALID_VISIBILITY = {"public", "department"}
_VALID_MEMBER_STATE = {"enabled", "disabled"}


class ToolRegistryError(Exception):
    """CRUD 业务错误基类;status_code 供 router 映射 HTTP。"""
    status_code = 400


class UnitNotFoundError(ToolRegistryError):
    status_code = 404


class SeededReadOnlyError(ToolRegistryError):
    status_code = 409


class NameCollisionError(ToolRegistryError):
    status_code = 409


class InvalidUnitError(ToolRegistryError):
    status_code = 400


class ImmutableFieldError(ToolRegistryError):
    """改了 create 后不可变的字段(如 kind —— 它决定 full_name 形状)。"""
    status_code = 409


class AgentNotFoundError(ToolRegistryError):
    status_code = 400


class ToolRegistryManager:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._registry = ToolRegistryRepository(session)
        self._creds = ToolCredentialRepository(session)

    # ======================================================================
    # 读 + 序列化
    # ======================================================================

    async def list_units(self) -> List[dict]:
        units = await self._registry.list_units()
        out = []
        for u in units:
            mounts = await self._registry.agent_units_for_unit(u.name)
            cred_map = await self._creds.placeholder_map(u.name)
            out.append(self._serialize_unit(u, mounts, cred_map))
        return out

    async def get_unit(self, name: str) -> dict:
        u = await self._require_unit(name)
        mounts = await self._registry.agent_units_for_unit(name)
        cred_map = await self._creds.placeholder_map(name)
        return self._serialize_unit(u, mounts, cred_map)

    async def list_agents(self) -> List[dict]:
        agents = await self._registry.list_agents()
        return [{"name": a.name, "description": a.description, "internal": a.internal}
                for a in agents]

    def _serialize_unit(self, u: ToolUnit, mounts: List[AgentUnit],
                        cred_map: Dict[str, str]) -> dict:
        members = sorted(u.members, key=lambda m: m.full_name)
        # 定义引用的 {{NAME}} ∪ 已配置的占位符 → 掩码列表(永不含密文/明文)
        referenced = self._referenced_placeholders(members)
        creds = [
            {"placeholder": ph, "configured": ph in cred_map, "source": cred_map.get(ph)}
            for ph in sorted(referenced | set(cred_map))
        ]
        return {
            "name": u.name,
            "kind": u.kind,
            "description": u.description,
            "visibility": u.visibility,
            "defer": u.defer,
            "provider": u.provider,
            "source": u.source,
            "members": [self._serialize_member(m) for m in members],
            "mounted_agents": [
                {"agent_name": au.agent_name, "member_state": au.member_state,
                 "source": au.source}
                for au in mounts
            ],
            "credentials": creds,
        }

    def _serialize_member(self, m: ToolMember) -> dict:
        d = m.definition or {}
        return {
            "member_name": m.member_name,
            "full_name": m.full_name,
            "permission": m.permission,
            "show_example": m.show_example,
            # definition 含 endpoint/headers(里头是 {{NAME}} 占位符,非明文 secret)+ params
            "definition": d,
        }

    # ======================================================================
    # unit CRUD(dynamic-only;seeded 只读)
    # ======================================================================

    async def create_unit(self, spec: dict) -> dict:
        name = (spec.get("name") or "").strip()
        members = self._build_members(name, spec)  # 校验 + 产 ToolMember(未入库)
        await self._validate_names(name, members, exclude_unit=None)

        unit = ToolUnit(
            name=name,
            kind=spec["kind"],
            description=spec.get("description", "") or "",
            visibility=self._check_visibility(spec.get("visibility", "public")),
            defer=bool(spec.get("defer", False)),
            provider="http",        # B-4 CRUD 只建 http;mcp 走 F
            source="dynamic",
            seed_hash=None,
        )
        self._registry.add_unit(unit, members)
        await self._commit("create unit")
        return await self.get_unit(name)

    async def update_unit(self, name: str, spec: dict) -> dict:
        u = await self._require_unit(name)
        self._require_dynamic(u, "edit")
        # kind 不可变:它决定 full_name 形状(singleton==unit 名 vs set=<unit>__<member>)。
        # 改 kind → replace_members 会静默重命名可调工具名,挂在旧 full_name 上的 always_allow
        # / 未来 per-tool 规则全悬空(reviewer #14)。要变 = 删了重建。
        if spec.get("kind") != u.kind:
            raise ImmutableFieldError(
                f"cannot change kind of unit '{name}' ('{u.kind}' → '{spec.get('kind')}') — "
                f"kind is immutable (it determines the callable tool name); delete and recreate"
            )
        members = self._build_members(name, spec)
        await self._validate_names(name, members, exclude_unit=name)

        u.description = spec.get("description", "") or ""
        u.visibility = self._check_visibility(spec.get("visibility", "public"))
        u.defer = bool(spec.get("defer", False))
        await self._registry.replace_members(name, members)
        # 新定义不再引用的 dynamic 凭证 → prune(与 reconciler 对 seeded 的 prune 对称,
        # 否则失引用密文残留、GET 仍显示 configured,误导 + secret 卫生 cruft,reviewer #9)
        await self._creds.prune_unreferenced(name, self._referenced_placeholders(members))
        await self._commit("update unit")
        return await self.get_unit(name)

    async def delete_unit(self, name: str) -> None:
        u = await self._require_unit(name)
        self._require_dynamic(u, "delete")
        # 显式删凭证 + 子行(dialect-safe);DB FK CASCADE 是双保险
        await self._creds.delete_for_unit(name)
        await self._registry.delete_unit(name)
        await self._commit("delete unit")

    # ======================================================================
    # agent 挂载(写 dynamic agent_unit;seeded[MD 来源]只读)
    # ======================================================================

    async def mount(self, unit_name: str, agent_name: str, member_state: str) -> dict:
        await self._require_unit(unit_name)
        if not await self._registry.agent_exists(agent_name):
            raise AgentNotFoundError(f"agent '{agent_name}' does not exist")
        if member_state not in _VALID_MEMBER_STATE:
            raise InvalidUnitError(
                f"member_state must be one of {sorted(_VALID_MEMBER_STATE)}"
            )
        existing = await self._registry.get_agent_unit(agent_name, unit_name)
        if existing is not None:
            if existing.source == "seeded":
                raise SeededReadOnlyError(
                    f"agent '{agent_name}' binds unit '{unit_name}' via its MD "
                    f"(seeded) — change the agent config, not via UI"
                )
            existing.member_state = member_state
        else:
            self._registry.add_agent_unit(AgentUnit(
                agent_name=agent_name,
                unit_name=unit_name,
                member_state=member_state,
                source="dynamic",
            ))
        await self._commit("mount unit")
        return {"agent_name": agent_name, "unit_name": unit_name,
                "member_state": member_state, "source": "dynamic"}

    async def unmount(self, unit_name: str, agent_name: str) -> None:
        existing = await self._registry.get_agent_unit(agent_name, unit_name)
        if existing is None:
            raise UnitNotFoundError(
                f"agent '{agent_name}' is not mounted on unit '{unit_name}'"
            )
        if existing.source == "seeded":
            raise SeededReadOnlyError(
                f"agent '{agent_name}' binds unit '{unit_name}' via its MD (seeded) — "
                f"cannot unmount via UI"
            )
        await self._registry.delete_agent_unit(agent_name, unit_name)
        await self._commit("unmount unit")

    # ======================================================================
    # 凭证(写-only:set 加密落库,GET 永不回明文 —— 见 _serialize_unit 掩码)
    # ======================================================================

    async def set_credential(self, unit_name: str, placeholder: str, value: str) -> None:
        u = await self._require_unit(unit_name)
        self._require_dynamic(u, "configure credentials for")
        if not value:
            raise InvalidUnitError("credential value must be non-empty")
        # 占位符必须被某成员的 endpoint/headers 引用 —— 否则是配不上的孤儿密文(GET 会显示
        # configured 却无对应 {{NAME}},误导 + secret cruft,reviewer #9)。
        if placeholder not in self._referenced_placeholders(u.members):
            raise InvalidUnitError(
                f"placeholder '{placeholder}' is not referenced by any endpoint/header in "
                f"unit '{unit_name}'; add the {{{{{placeholder}}}}} reference first"
            )
        # 主密钥由 validate_config 强制存在 → get_cipher 不因缺 key 抛(缺 = 启动期已拦)。
        cipher = get_cipher()
        await self._creds.upsert(unit_name, placeholder, cipher.encrypt(value), "dynamic")
        await self._commit("set credential")

    async def delete_credential(self, unit_name: str, placeholder: str) -> None:
        u = await self._require_unit(unit_name)
        # seeded 凭证归 reconciler 拥有,UI 删了下次 reconcile 又种回 → 禁(对称 set,reviewer #2)
        self._require_dynamic(u, "delete credentials for")
        deleted = await self._creds.delete_placeholder(unit_name, placeholder)
        if not deleted:
            # 删不存在的占位符 = no-op,返 404(对称 unmount,不给假"已删")
            raise UnitNotFoundError(
                f"unit '{unit_name}' has no credential for placeholder '{placeholder}'"
            )
        await self._commit("delete credential")

    # ======================================================================
    # 校验 + 内部
    # ======================================================================

    async def _require_unit(self, name: str) -> ToolUnit:
        u = await self._registry.get_unit(name)
        if u is None:
            raise UnitNotFoundError(f"tool unit '{name}' not found")
        return u

    def _require_dynamic(self, u: ToolUnit, action: str) -> None:
        if u.source != "dynamic":
            raise SeededReadOnlyError(
                f"tool unit '{u.name}' is seeded from config (read-only); "
                f"cannot {action} it via UI — edit config/tools and re-run reconcile"
            )

    def _check_visibility(self, vis: str) -> str:
        if vis not in _VALID_VISIBILITY:
            raise InvalidUnitError(
                f"visibility must be one of {sorted(_VALID_VISIBILITY)}"
            )
        return vis

    def _build_members(self, unit_name: str, spec: dict) -> List[ToolMember]:
        """从 spec 产 ToolMember 行(校验 kind/permission/参数;算 full_name)。未入库。"""
        kind = spec.get("kind")
        if kind not in ("tool", "toolset"):
            raise InvalidUnitError("kind must be 'tool' or 'toolset'")
        raw_members = spec.get("members") or []
        if not raw_members:
            raise InvalidUnitError("a unit needs at least one member")
        if kind == "tool" and len(raw_members) != 1:
            raise InvalidUnitError("a singleton tool unit must have exactly one member")

        out: List[ToolMember] = []
        seen: set = set()
        for rm in raw_members:
            mname = (rm.get("member_name") or "").strip()
            if not mname:
                raise InvalidUnitError("member missing 'member_name'")
            if kind == "tool":
                # singleton:成员名规整为 unit 名,full_name == unit 名(无 `__`)
                mname = unit_name
                full_name = unit_name
            else:
                full_name = f"{unit_name}__{mname}"
            if mname in seen:
                raise InvalidUnitError(f"duplicate member_name '{mname}'")
            seen.add(mname)

            permission = rm.get("permission", "confirm")
            if permission not in _VALID_PERMISSIONS:
                raise InvalidUnitError(
                    f"member '{mname}' permission must be auto|confirm"
                )
            out.append(ToolMember(
                unit_name=unit_name,
                member_name=mname,
                full_name=full_name,
                permission=permission,
                definition=self._build_definition(rm),
                show_example=bool(rm.get("show_example", True)),
            ))
        return out

    def _build_definition(self, rm: dict) -> dict:
        params = []
        for p in rm.get("parameters", []) or []:
            ptype = p.get("type", "string")
            if ptype not in _VALID_PARAM_TYPES:
                raise InvalidUnitError(
                    f"unsupported parameter type '{ptype}' (valid: "
                    f"{sorted(_VALID_PARAM_TYPES)})"
                )
            if not p.get("name"):
                raise InvalidUnitError("parameter missing 'name'")
            # {{...}} 只在 endpoint/headers 受支持(运行期替换);参数 default 不是 secret
            # 注入点 —— 出现占位符 = 配错(会原样外发,且不被 substitute),build 期拒(sweep)
            if extract_placeholders(p.get("default")):
                raise InvalidUnitError(
                    f"parameter '{p['name']}' default must not contain a {{{{...}}}} placeholder "
                    f"(secret placeholders are only supported in endpoint/headers)"
                )
            params.append({
                "name": p["name"],
                "type": ptype,
                "description": p.get("description", ""),
                "required": p.get("required", True),
                "default": p.get("default"),
                "enum": p.get("enum"),
            })
        endpoint = rm.get("endpoint", "") or ""
        headers = rm.get("headers", {}) or {}
        # SSRF-02 前缀闸:与 seeds/loader 同口径,{{VAR}} 必须白名单前缀(reviewer #15)。
        # 否则 dynamic 路径能把凭证存到 {{JWT_SECRET}} 这类误导名下,且将来若为 dynamic
        # 重引入 env 解析即成外泄面。失败转 400(不漏 500)。
        try:
            assert_secret_refs_allowed(endpoint)
            assert_secret_refs_allowed(headers)
        except SecretResolutionError as e:
            raise InvalidUnitError(str(e)) from e
        # response_extract(JMESPath)语法在保存期 loud-fail(→400),与 seeds 同口径
        try:
            validate_response_extract(rm.get("response_extract"))
        except ValueError as e:
            raise InvalidUnitError(str(e)) from e
        return {
            "description": rm.get("description", "") or "",
            "endpoint": endpoint,
            "method": (rm.get("method", "GET") or "GET").upper(),
            "headers": headers,
            "parameters": params,
            "response_extract": rm.get("response_extract"),
            "timeout": int(rm.get("timeout", 60) or 60),
        }

    def _referenced_placeholders(self, members) -> set:
        """成员 endpoint/headers 引用的 {{NAME}} 占位符全集(凭证掩码 / 引用校验 / prune 共用)。"""
        refs: set = set()
        for m in members:
            d = m.definition or {}
            refs |= extract_placeholders(d.get("endpoint", ""))
            refs |= extract_placeholders(d.get("headers", {}) or {})
        return refs

    async def _validate_names(self, name: str, members: List[ToolMember],
                              exclude_unit: Optional[str]) -> None:
        """撞名 by-construction 闸:unit 名 + 每个 full_name ∉ builtin∪reserved∪已存 external。"""
        if not name:
            raise InvalidUnitError("unit name is required")
        if "__" in name:
            raise InvalidUnitError("unit name must not contain '__' (prefix separator)")
        if is_builtin_name(name):
            raise NameCollisionError(
                f"unit name '{name}' collides with a builtin/reserved tool name"
            )
        if exclude_unit != name and name in await self._registry.existing_unit_names():
            raise NameCollisionError(f"tool unit '{name}' already exists")

        existing_full = await self._registry.existing_full_names(exclude_unit=exclude_unit)
        for m in members:
            if is_builtin_name(m.full_name):
                raise NameCollisionError(
                    f"tool full_name '{m.full_name}' collides with a builtin/reserved name"
                )
            if m.full_name in existing_full:
                raise NameCollisionError(
                    f"tool full_name '{m.full_name}' already used by unit "
                    f"'{existing_full[m.full_name]}'"
                )

    async def _commit(self, what: str) -> None:
        try:
            await self._session.commit()
        except IntegrityError as e:
            await self._session.rollback()
            # DB unique 兜并发 TOCTOU(撞名预检与写入之间的竞态)
            raise NameCollisionError(
                f"{what} failed: a unit/tool with that name already exists"
            ) from e

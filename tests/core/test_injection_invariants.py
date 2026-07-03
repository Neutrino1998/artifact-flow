"""请求级 builtin 注入不变量单测(F-0)。

收口前注入散两家:search_tools 在 resolver、read_skill/mount_skill 在 controller_factory
手动 setdefault,且 activate_skill 变异 permissions 后不重跑任何注入判断 → 两条运行时
不对称(skill 翻开 bash 后 mount_skill 不连动 / 翻开 defer unit 后 DeferredUnit 不注册、
search_tools 不注入)。本文件先按修后契约钉死两条运行时路径,再覆盖 resolve 期注入的
搬家等价(read_skill 全 agent / mount_skill 仅 bash agent)与 loud-fail 契约。
"""

import pytest

from core.effective_toolset import (
    DeferredUnit,
    EffectiveToolset,
    SkillGrant,
    resolve_all,
    resolve_effective_toolset,
)
from reconcile.snapshot import AgentSnapshot, RegistrySnapshot, SkillInfo, UnitInfo
from tools.base import ToolPermission


class _Tool:
    def __init__(self, name, permission):
        self.name = name
        self.permission = permission


def _agent(name="lead_agent", builtin_tools=None, units=None):
    return AgentSnapshot(
        name=name, description="d", model="m", max_tool_rounds=10, internal=False,
        role_prompt="", builtin_tools=builtin_tools or {}, units=units or {},
    )


def _unit(name, members, kind="toolset", defer=False, description=""):
    return UnitInfo(
        name=name, kind=kind, description=description, visibility="public",
        defer=defer, provider="http", source="seeded", member_full_names=list(members),
    )


def _snapshot(units=None, agents=None):
    return RegistrySnapshot(
        external_tools={},
        units={u.name: u for u in (units or [])},
        agents={a.name: a for a in (agents or [])},
    )


def _skill(slug, allowed):
    return SkillInfo(
        slug=slug, name=slug, description="", visibility="public",
        default_enabled=True, owner_user_id=None, allowed_tools=allowed,
    )


# ============================================================
# resolve 期注入(controller_factory setdefault 搬进 resolver 的行为等价)
# ============================================================


def test_read_skill_injected_into_every_agent_at_resolve():
    # read_skill 在 tools(= 有可见 skill)→ 每个 agent 都注入,含无任何工具的 agent
    tools = {"read_skill": _Tool("read_skill", ToolPermission.AUTO),
             "web_search": _Tool("web_search", ToolPermission.AUTO)}
    snap = _snapshot(agents=[
        _agent("lead_agent", builtin_tools={"web_search": "enabled"}),
        _agent("research_agent"),
    ])
    result = resolve_all(snap, tools)
    assert all("read_skill" in ets for ets in result.values())
    assert result["lead_agent"].level("read_skill") == ToolPermission.AUTO


def test_read_skill_not_injected_when_absent_from_tools():
    # 无可见 skill(工具没建)→ 不注入
    tools = {"web_search": _Tool("web_search", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(
        _agent(builtin_tools={"web_search": "enabled"}), _snapshot(), tools
    )
    assert "read_skill" not in eff


def test_mount_skill_only_for_bash_agents_at_resolve():
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM),
             "mount_skill": _Tool("mount_skill", ToolPermission.AUTO)}
    snap = _snapshot(agents=[
        _agent("lead_agent", builtin_tools={"bash": "enabled"}),
        _agent("research_agent"),  # 无 bash
    ])
    result = resolve_all(snap, tools)
    assert "mount_skill" in result["lead_agent"]
    assert "mount_skill" not in result["research_agent"]


# ============================================================
# 运行时不对称 ①:skill 翻开 bash → mount_skill 连动(修前不连动)
# ============================================================


def test_activate_skill_opening_bash_injects_mount_skill():
    agent = _agent(builtin_tools={"bash": "disabled"})
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM),
             "mount_skill": _Tool("mount_skill", ToolPermission.AUTO),
             "read_skill": _Tool("read_skill", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(agent, _snapshot(), tools, {"s": _skill("s", ["bash"])})

    # resolve 期:无 bash → mount_skill 不注入(不给死能力);read_skill 照注
    assert "bash" not in eff and "mount_skill" not in eff
    assert "read_skill" in eff

    eff.activate_skill("s")
    # 激活翻开 bash → 不变量重跑,mount_skill 连动注入
    assert "bash" in eff and eff.level("bash") == ToolPermission.CONFIRM
    assert "mount_skill" in eff and eff.level("mount_skill") == ToolPermission.AUTO


def test_activate_skill_opening_bash_no_mount_skill_when_no_bundle():
    # mount_skill 不在 tools(无可见 bundle skill)→ 翻开 bash 也不凭空造
    agent = _agent(builtin_tools={"bash": "disabled"})
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM),
             "read_skill": _Tool("read_skill", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(agent, _snapshot(), tools, {"s": _skill("s", ["bash"])})
    eff.activate_skill("s")
    assert "bash" in eff
    assert "mount_skill" not in eff


# ============================================================
# 运行时不对称 ②:skill 翻开 defer unit → DeferredUnit 注册 + search_tools 注入
# (修前:成员全 schema 入上下文、search_tools 缺席)
# ============================================================


def test_activate_skill_opening_deferred_unit_keeps_progressive_disclosure():
    unit = _unit("github", ["github__list", "github__create"], defer=True, description="GH")
    agent = _agent(units={"github": "disabled"})
    tools = {"github__list": _Tool("github__list", ToolPermission.AUTO),
             "github__create": _Tool("github__create", ToolPermission.CONFIRM),
             "search_tools": _Tool("search_tools", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(
        agent, _snapshot(units=[unit]), tools, {"s": _skill("s", ["github"])}
    )

    # resolve 期:unit disabled → 成员不可调、无 deferred 索引、无 search_tools
    assert "github__list" not in eff
    assert "github" not in eff.deferred_units
    assert "search_tools" not in eff
    # 授予随带 DeferredUnit(与 resolve ② 同判据)
    assert "github" in eff.skill_grants["s"].deferred_units

    eff.activate_skill("s")
    # 成员可调 + 索引行注册(渲染保持 index-row,不落全 schema)+ search_tools 连动
    assert "github__list" in eff and "github__create" in eff
    assert eff.deferred_units["github"].member_full_names == ["github__list", "github__create"]
    assert eff.deferred_member_names() == {"github__list", "github__create"}
    assert "search_tools" in eff


def test_grant_for_non_deferred_unit_carries_no_deferred_entry():
    unit = _unit("github", ["github__list"], defer=False)
    agent = _agent(units={"github": "disabled"})
    tools = {"github__list": _Tool("github__list", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(
        agent, _snapshot(units=[unit]), tools, {"s": _skill("s", ["github"])}
    )
    assert eff.skill_grants["s"].deferred_units == {}
    eff.activate_skill("s")
    assert "github__list" in eff
    assert eff.deferred_units == {}


def test_activation_does_not_clobber_existing_deferred_unit():
    # 同名 unit 已在 resolve 期 deferred(enabled)→ 激活 setdefault 不覆盖
    du = DeferredUnit(name="github", description="orig", member_full_names=["github__list"])
    eff = EffectiveToolset(
        permissions={"github__list": ToolPermission.AUTO},
        deferred_units={"github": du},
        skill_grants={"s": SkillGrant(
            permissions={"github__list": ToolPermission.AUTO},
            deferred_units={"github": DeferredUnit(
                name="github", description="other", member_full_names=["github__list"])},
        )},
        injectable_builtins={"search_tools": ToolPermission.AUTO},
    )
    eff.activate_skill("s")
    assert eff.deferred_units["github"] is du


# ============================================================
# 幂等 + loud-fail 契约
# ============================================================


def test_activate_skill_idempotent_across_invariant_rerun():
    unit = _unit("github", ["github__list"], defer=True)
    agent = _agent(builtin_tools={"bash": "disabled"}, units={"github": "disabled"})
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM),
             "github__list": _Tool("github__list", ToolPermission.AUTO),
             "search_tools": _Tool("search_tools", ToolPermission.AUTO),
             "mount_skill": _Tool("mount_skill", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(
        agent, _snapshot(units=[unit]), tools, {"s": _skill("s", ["bash", "github"])}
    )
    eff.activate_skill("s")
    perms, deferred = dict(eff.permissions), dict(eff.deferred_units)
    eff.activate_skill("s")
    assert eff.permissions == perms
    assert eff.deferred_units == deferred


def test_deferred_grant_without_search_tools_registered_is_loud():
    # deferred ⟹ search_tools 是硬 bug 档:search_tools 没注册时激活翻开 defer unit
    # 必须当场 KeyError(不静默产出无法 search 的死披露),同 resolve 期 2026-06-26 契约。
    eff = EffectiveToolset(
        permissions={},
        skill_grants={"s": SkillGrant(
            permissions={"github__list": ToolPermission.AUTO},
            deferred_units={"github": DeferredUnit(
                name="github", description="", member_full_names=["github__list"])},
        )},
        injectable_builtins={},  # search_tools 缺席 = 未注册
    )
    with pytest.raises(KeyError):
        eff.activate_skill("s")

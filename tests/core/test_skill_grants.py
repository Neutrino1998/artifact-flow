"""skill_grants 预烤 + activate_skill 单测(C-2,决策 11)。

覆盖:skill 只翻 agent disabled 池(enabled no-op / absent 翻不开)、builtin singleton 与
external unit 两路、等级取自工具对象、resolve_all 透传、activate_skill 合并 + 幂等。
"""

from core.effective_skillset import resolve_effective_skillset
from core.effective_toolset import resolve_all, resolve_effective_toolset
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


def _unit(name, members, kind="toolset", defer=False):
    return UnitInfo(
        name=name, kind=kind, description="", visibility="public", defer=defer,
        provider="http", source="seeded", member_full_names=list(members),
    )


def _snapshot(units=None, agents=None):
    return RegistrySnapshot(
        external_tools={},
        units={u.name: u for u in (units or [])},
        agents={a.name: a for a in (agents or [])},
    )


def _skill(slug, allowed, visibility="public"):
    return SkillInfo(
        slug=slug, name=slug, description="", visibility=visibility,
        default_enabled=True, owner_user_id=None, allowed_tools=allowed,
    )


def test_grant_flips_disabled_builtin():
    agent = _agent(builtin_tools={"bash": "disabled", "web_search": "enabled"})
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM),
             "web_search": _Tool("web_search", ToolPermission.AUTO)}
    skills = {"s": _skill("s", ["bash"])}
    eff = resolve_effective_toolset(agent, _snapshot(), tools, skills)

    # bash disabled → 不在初始可调集,但在 skill_grants
    assert "bash" not in eff
    assert eff.skill_grants["s"] == {"bash": ToolPermission.CONFIRM}

    eff.activate_skill("s")
    assert "bash" in eff
    assert eff.level("bash") == ToolPermission.CONFIRM


def test_enabled_tool_not_in_grants():
    # skill 点名已 enabled 的工具 → 不进 grants(已可调,no-op)
    agent = _agent(builtin_tools={"web_search": "enabled"})
    tools = {"web_search": _Tool("web_search", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(agent, _snapshot(), tools, {"s": _skill("s", ["web_search"])})
    assert "s" not in eff.skill_grants


def test_absent_tool_cannot_be_granted():
    # skill 点名 agent 宇宙外工具 → 翻不开(不引入 absent)
    agent = _agent(builtin_tools={"web_search": "enabled"})
    tools = {"web_search": _Tool("web_search", ToolPermission.AUTO),
             "bash": _Tool("bash", ToolPermission.CONFIRM)}
    eff = resolve_effective_toolset(agent, _snapshot(), tools, {"s": _skill("s", ["bash"])})
    assert "s" not in eff.skill_grants


def test_grant_flips_disabled_external_unit_all_members():
    unit = _unit("github", ["github__list", "github__create"])
    agent = _agent(units={"github": "disabled"})
    tools = {"github__list": _Tool("github__list", ToolPermission.AUTO),
             "github__create": _Tool("github__create", ToolPermission.CONFIRM)}
    skills = {"s": _skill("s", ["github"])}  # 整 unit 名
    eff = resolve_effective_toolset(agent, _snapshot(units=[unit]), tools, skills)

    assert "github__list" not in eff
    assert eff.skill_grants["s"] == {
        "github__list": ToolPermission.AUTO,
        "github__create": ToolPermission.CONFIRM,
    }
    eff.activate_skill("s")
    assert "github__list" in eff and "github__create" in eff


def test_full_name_entry_resolves_to_unit():
    # allowed-tools 写成员全名 → 归属整 unit(整 unit 翻开,决策 11)
    unit = _unit("github", ["github__list", "github__create"])
    agent = _agent(units={"github": "disabled"})
    tools = {"github__list": _Tool("github__list", ToolPermission.AUTO),
             "github__create": _Tool("github__create", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(
        agent, _snapshot(units=[unit]), tools, {"s": _skill("s", ["github__list"])}
    )
    assert set(eff.skill_grants["s"]) == {"github__list", "github__create"}


def test_no_skill_snapshot_empty_grants():
    agent = _agent(builtin_tools={"bash": "disabled"})
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM)}
    eff = resolve_effective_toolset(agent, _snapshot(), tools)
    assert eff.skill_grants == {}


def test_activate_unknown_slug_noop():
    agent = _agent(builtin_tools={"web_search": "enabled"})
    tools = {"web_search": _Tool("web_search", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(agent, _snapshot(), tools, {})
    before = dict(eff.permissions)
    eff.activate_skill("nope")
    assert eff.permissions == before


def test_resolve_all_threads_skill_snapshot():
    agent = _agent(builtin_tools={"bash": "disabled"})
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM)}
    snap = _snapshot(agents=[agent])
    result = resolve_all(snap, tools, skill_snapshot={"s": _skill("s", ["bash"])})
    assert result["lead_agent"].skill_grants["s"] == {"bash": ToolPermission.CONFIRM}


def test_grants_baked_only_for_visible_skills():
    """controller_factory 组合(Finding 1):full snapshot → EffectiveSkillSet → 只从
    visible 子集烤授予。看不见的 skill(dept 无 grant)其授予不烤 → 跨回合恢复 active_skills
    时 activate_skill 对它是空操作(能力跟随可见性,by-construction)。"""
    full = {
        "pub": _skill("pub", ["bash"], visibility="public"),        # 可见
        "dept": _skill("dept", ["bash"], visibility="department"),  # 无 grant → 不可见
    }
    eff_skill = resolve_effective_skillset("u1", full, {}, dept_matched=set())
    assert "pub" in eff_skill.visible and "dept" not in eff_skill.visible

    # 复刻 controller_factory:只把 visible 子集喂进 resolver
    visible_snap = {s: full[s] for s in eff_skill.visible}
    agent = _agent(builtin_tools={"bash": "disabled"})
    tools = {"bash": _Tool("bash", ToolPermission.CONFIRM)}
    eff = resolve_effective_toolset(agent, _snapshot(), tools, visible_snap)

    assert "pub" in eff.skill_grants        # 可见 → 烤了
    assert "dept" not in eff.skill_grants   # 不可见 → 没烤(即便在 full snapshot 里)
    # 恢复被撤销 skill 的 slug → 空操作,bash 仍不可调
    eff.activate_skill("dept")
    assert "bash" not in eff
